#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
docs viewer - 로컬 문서 뷰어 (단일 파일 / 표준 라이브러리만 사용)

  python3 docs_viewer.py ~/docs ~/workspace/notes
  python3 docs_viewer.py --no-token --port 8765 .

지원 포맷
  Markdown(.md/.markdown/.mdx, mermaid 다이어그램 포함), HTML, 텍스트/코드,
  이미지, PDF, CSV,
  docx/xlsx/pptx (LibreOffice soffice 가 PATH 에 있을 때만),
  Google Drive (옵션, OAuth 설정 시)

설정/상태 파일 (모두 스크립트와 같은 폴더)
  config.json           기본 폴더·포트·옵션
  gdrive_client.json    Google OAuth 클라이언트 (선택)
  gdrive_token.json     Drive refresh token (0600, 자동 생성)
  cache/                office 변환 캐시

보안
  - 127.0.0.1 에만 바인딩 (--host 로 변경 가능하나 권장하지 않음)
  - Host 헤더 검증으로 DNS rebinding 차단
  - 실행마다 랜덤 토큰 발급 (쿠키 또는 ?t= 로 전달)
  - 모든 경로는 realpath 후 root 하위인지 검증 (심볼릭 링크 탈출 차단)
  - 문서 안의 HTML 은 태그 allowlist 로 살균, .html 파일은 스크립트 차단 iframe 에서 렌더
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import html as html_mod
import io
import json
import mimetypes
import os
import posixpath
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from html.parser import HTMLParser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

APP = "docs_viewer"
APP_TITLE = "docs viewer"
VERSION = "1.1"
BASE = Path(__file__).resolve().parent          # 스크립트가 있는 작업 폴더
# 설정/캐시/Drive 토큰은 모두 작업 폴더 안에 둔다 (DOCS_VIEWER_HOME 으로 변경 가능)
HOME = Path(os.environ.get("DOCS_VIEWER_HOME") or BASE)
CACHE = HOME / "cache"
ASSETS = HOME / "assets"
DEFAULT_PORT = 8765

# mermaid 다이어그램은 파이썬만으로 그릴 수 없어 브라우저에서 렌더한다.
# 스크립트는 assets/ 에 두면 그것을 쓰고, 없으면 처음 필요할 때 한 번만
# CDN 에서 cache/ 로 내려받는다 (--no-mermaid 로 끄면 코드 블록으로 남는다).
MERMAID_VER = "11.17.2"
MERMAID_FILE = "mermaid-%s.min.js" % MERMAID_VER
MERMAID_URL = "https://cdn.jsdelivr.net/npm/mermaid@%s/dist/mermaid.min.js" % MERMAID_VER
MERMAID_MIN_BYTES = 512 * 1024   # 받다 만 파일을 캐시로 인정하지 않으려는 하한


# ---------------------------------------------------------------- 전역 설정
class Config(object):
    def __init__(self):
        self.roots = []          # [{'id','name','path'}]
        self.token = ""
        self.port = DEFAULT_PORT
        self.host = "127.0.0.1"
        self.md_unsafe = False   # md 안의 raw HTML 을 살균 없이 통과
        self.show_hidden = False
        self.soffice = None
        self.lan = False         # 루프백 밖으로 바인딩했는지 (LAN 노출 모드)
        self.oauth_state = ""    # OAuth CSRF 방지용 난수 (토큰과 별개)
        self.drive_tab = None    # None=자동(클라이언트 있으면 표시), True/False=강제
        self.allow_edit = False  # 분할 편집기에서 파일 저장 허용
        self.host_names = set()  # Host 헤더로 허용할 이름 (호스트명 등)
        self.mermaid = True      # mermaid 펜스를 다이어그램으로 렌더


CFG = Config()


# ---------------------------------------------------------------- 파일 종류
MD_EXT = {".md", ".markdown", ".mdown", ".mkd", ".mdx"}
HTML_EXT = {".html", ".htm", ".xhtml"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".ico", ".avif"}
PDF_EXT = {".pdf"}
CSV_EXT = {".csv", ".tsv"}
OFFICE_EXT = {".docx", ".doc", ".rtf", ".odt", ".xlsx", ".xls", ".ods",
              ".pptx", ".ppt", ".odp"}
# 슬라이드만 html 이 아니라 pdf 로 변환한다 (이유는 office_convert 주석 참고)
SLIDE_EXT = {".pptx", ".ppt", ".odp"}
# 시트가 여러 장인 문서 (변환 결과에서 시트 목록을 뽑아 툴바에 붙인다)
SHEET_EXT = {".xlsx", ".xls", ".ods"}
# Google Drive for Desktop 이 만드는 "바로가기" 파일 (내용은 doc_id 만 든 JSON)
GSTUB_EXT = {".gdoc", ".gsheet", ".gslides", ".gdraw", ".gform", ".gtable", ".gjam",
             ".gsite", ".gscript", ".gmap"}
GSTUB_URL = {
    ".gdoc": "https://docs.google.com/document/d/%s/edit",
    ".gsheet": "https://docs.google.com/spreadsheets/d/%s/edit",
    ".gslides": "https://docs.google.com/presentation/d/%s/edit",
    ".gdraw": "https://docs.google.com/drawings/d/%s/edit",
    ".gform": "https://docs.google.com/forms/d/%s/edit",
    ".gscript": "https://script.google.com/d/%s/edit",
}
GSTUB_LABEL = {".gdoc": "Google 문서", ".gsheet": "Google 스프레드시트",
               ".gslides": "Google 프레젠테이션", ".gdraw": "Google 그림",
               ".gform": "Google 설문지", ".gscript": "Apps Script"}
TEXT_EXT = {
    ".txt", ".text", ".log", ".json", ".jsonc", ".json5", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".conf", ".properties", ".env", ".xml", ".sql", ".sh", ".bash", ".zsh", ".fish",
    ".ps1", ".bat", ".py", ".pyi", ".rb", ".pl", ".php", ".java", ".kt", ".kts", ".scala",
    ".groovy", ".gradle", ".c", ".h", ".cc", ".cpp", ".hpp", ".cs", ".go", ".rs", ".swift", ".m",
    ".mm", ".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".css", ".scss",
    ".sass", ".less", ".dart", ".lua", ".r", ".jl", ".ex", ".exs", ".erl", ".hs", ".clj", ".vim",
    ".diff", ".patch", ".rst", ".adoc", ".tex", ".ipynb", ".gitignore", ".dockerignore",
    ".editorconfig",
}
TEXT_NAMES = {
    "makefile", "dockerfile", "jenkinsfile", "readme", "license", "licence", "notice", "changelog",
    "authors", "contributing", "todo", "vagrantfile", "procfile", "gemfile", "rakefile", "brewfile",
    ".gitignore", ".gitattributes", ".dockerignore", ".editorconfig", ".env", ".npmrc", ".babelrc",
    ".prettierrc", ".eslintrc",
}
SKIP_DIRS = {
    ".git", ".svn", ".hg", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache",
    ".pytest_cache", ".gradle", ".idea", ".vscode-test", "build", "dist", "out", "target",
    ".next", ".nuxt", ".cache", "bower_components", ".terraform",
}
MAX_TEXT_BYTES = 4 * 1024 * 1024
MAX_SEARCH_BYTES = 2 * 1024 * 1024


def kind_of(p):
    """확장자/이름으로 문서 종류 판별."""
    name = p.name.lower()
    ext = p.suffix.lower()
    if ext in MD_EXT:
        return "md"
    if ext in HTML_EXT:
        return "html"
    if ext in IMG_EXT:
        return "image"
    if ext in PDF_EXT:
        return "pdf"
    if ext in CSV_EXT:
        return "csv"
    if ext in OFFICE_EXT:
        return "office"
    if ext in GSTUB_EXT:
        return "gstub"
    if ext in TEXT_EXT or name in TEXT_NAMES:
        return "text"
    return "binary"


def searchable(p):
    return kind_of(p) in ("md", "html", "text", "csv")


def materialized(st):
    """파일 내용이 실제로 로컬 디스크에 있는지.

    Google Drive for Desktop 스트리밍 모드(및 iCloud/OneDrive 등)는 아직 내려오지 않은
    파일을 '논리 크기는 있지만 할당 블록은 0' 인 placeholder 로 노출한다. 이걸 읽으면
    그 순간 네트워크 다운로드가 발생하므로, 내용 검색 대상에서 제외한다.
    미러링 모드/오프라인 고정된 파일은 블록이 할당돼 있어 정상적으로 검색된다.
    """
    if st.st_size == 0:
        return True
    return getattr(st, "st_blocks", 1) > 0


def drive_mounts():
    """Google Drive for Desktop 마운트 경로 탐색 (macOS / Windows / Linux)."""
    out = []
    cs = Path.home() / "Library" / "CloudStorage"
    if cs.is_dir():
        for d in sorted(cs.iterdir()):
            if d.name.startswith("GoogleDrive-") and d.is_dir():
                out.append(d)
    for cand in (Path.home() / "Google Drive", Path("/Volumes/GoogleDrive"),
                 Path.home() / "GoogleDrive"):
        try:
            if cand.is_dir() and Path(os.path.realpath(str(cand))) not in [
                    Path(os.path.realpath(str(x))) for x in out]:
                out.append(cand)
        except OSError:
            pass
    return out


def read_gstub(p):
    """.gdoc/.gsheet 스텁에서 doc_id 와 웹 URL 을 뽑는다."""
    ext = p.suffix.lower()
    try:
        info = json.loads(p.read_text("utf-8", "replace"))
    except Exception:
        info = {}
    doc_id = info.get("doc_id") or info.get("resource_id") or ""
    if not doc_id and info.get("url"):
        m = ID_RE.search(info["url"])
        doc_id = m.group(1) if m else ""
    url = info.get("url") or ""
    if not url and doc_id:
        url = GSTUB_URL.get(ext, "https://drive.google.com/open?id=%s") % doc_id
    return {
        "driveId": doc_id,
        "webViewLink": url,
        "docType": GSTUB_LABEL.get(ext, "Google Drive 문서"),
        "email": info.get("email", ""),
    }


# ---------------------------------------------------------------- 경로 안전장치
def root_by_id(rid):
    for r in CFG.roots:
        if r["id"] == rid:
            return r
    return None


def safe_path(rid, rel):
    """root 하위로 한정된 실제 경로. 벗어나면 PermissionError."""
    root = root_by_id(rid)
    if root is None:
        raise KeyError("unknown root: %s" % rid)
    rel = (rel or "").replace("\\", "/")
    rel = posixpath.normpath("/" + rel).lstrip("/")      # ../ 정리
    if not rel:
        return root["path"]
    target = Path(os.path.realpath(str(root["path"] / rel)))
    base = root["path"]
    if target != base and base not in target.parents:
        raise PermissionError("path escapes root: %s" % rel)
    return target


def rel_of(rid, path):
    root = root_by_id(rid)
    if root is None:
        return ""
    try:
        r = str(Path(path).relative_to(root["path"]))
    except Exception:
        return ""
    return "" if r == "." else r.replace(os.sep, "/")


def fmt_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return "%d B" % n


def file_encoding(path, limit=65536):
    """저장할 때 원본 인코딩을 유지하기 위한 간단한 판별."""
    try:
        with path.open("rb") as fh:
            data = fh.read(limit)
    except OSError:
        return "utf-8"
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            data.decode(enc)
            return enc
        except UnicodeDecodeError:
            continue
    return "utf-8"


def read_text(path, limit=MAX_TEXT_BYTES):
    with path.open("rb") as fh:
        data = fh.read(limit + 1)
    truncated = len(data) > limit
    data = data[:limit]
    for enc in ("utf-8", "utf-8-sig", "cp949", "euc-kr"):
        try:
            return data.decode(enc), truncated
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace"), truncated


# ---------------------------------------------------------------- HTML 살균기
ALLOWED_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "caption", "center", "code", "col", "colgroup", "dd",
    "del", "details", "dfn", "div", "dl", "dt", "em", "figcaption", "figure", "h1", "h2", "h3",
    "h4", "h5", "h6", "hr", "i", "img", "ins", "kbd", "li", "mark", "ol", "p", "pre", "q", "s",
    "samp", "small", "span", "strong", "sub", "summary", "sup", "table", "tbody", "td", "tfoot",
    "th", "thead", "tr", "u", "ul", "var", "wbr",
}
DROP_CONTENT_TAGS = {"script", "style", "iframe", "object", "embed", "template", "noscript",
                     "svg", "math", "form", "input", "button", "select", "textarea"}
VOID_TAGS = {"br", "hr", "img", "wbr", "col"}
GLOBAL_ATTRS = {"id", "class", "title", "dir", "lang", "align"}
TAG_ATTRS = {
    "a": {"href", "target", "rel", "name"},
    "img": {"src", "alt", "width", "height", "loading"},
    "td": {"colspan", "rowspan", "valign"},
    "th": {"colspan", "rowspan", "valign", "scope"},
    "ol": {"start", "type", "reversed"},
    "details": {"open"},
    "code": {"data-lang"},
}
BAD_SCHEME = re.compile(r"^(javascript|vbscript|data|file|blob):", re.I)


def safe_url(u, allow_data_image=False):
    if not u:
        return None
    u = u.strip().replace("\x00", "")
    compact = re.sub(r"\s", "", u)
    if allow_data_image and compact.lower().startswith("data:image/"):
        return u
    if BAD_SCHEME.match(compact):
        return None
    return u


class Sanitizer(HTMLParser):
    """태그 allowlist 살균기. 허용 안 된 태그는 벗겨내고 내용만 남긴다."""

    def __init__(self, link_resolver=None):
        HTMLParser.__init__(self, convert_charrefs=True)
        self.out = []
        self.open_tags = []
        self.drop_depth = 0
        self.resolve = link_resolver

    def _attrs(self, tag, attrs):
        allowed = GLOBAL_ATTRS | TAG_ATTRS.get(tag, set())
        parts = []
        for k, v in attrs:
            k = (k or "").lower()
            if k.startswith("on") or k not in allowed:
                continue
            v = v or ""
            if k == "href":
                v2 = safe_url(v)
                if v2 is None:
                    continue
                if self.resolve:
                    v2, extra = self.resolve(v2, False)
                    for ek, ev in extra.items():
                        parts.append('%s="%s"' % (ek, html_mod.escape(ev, True)))
                v = v2
            elif k == "src":
                v2 = safe_url(v, allow_data_image=True)
                if v2 is None:
                    continue
                if self.resolve:
                    v2, _extra = self.resolve(v2, True)
                v = v2
            parts.append('%s="%s"' % (k, html_mod.escape(v, True)))
        return (" " + " ".join(parts)) if parts else ""

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in DROP_CONTENT_TAGS:
            self.drop_depth += 1
            return
        if self.drop_depth or tag not in ALLOWED_TAGS:
            return
        if tag in VOID_TAGS:
            self.out.append("<%s%s>" % (tag, self._attrs(tag, attrs)))
        else:
            self.open_tags.append(tag)
            self.out.append("<%s%s>" % (tag, self._attrs(tag, attrs)))

    def handle_startendtag(self, tag, attrs):
        tag = tag.lower()
        if self.drop_depth or tag not in ALLOWED_TAGS or tag in DROP_CONTENT_TAGS:
            return
        self.out.append("<%s%s>" % (tag, self._attrs(tag, attrs)))

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in DROP_CONTENT_TAGS:
            if self.drop_depth:
                self.drop_depth -= 1
            return
        if self.drop_depth or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        if tag in self.open_tags:
            while self.open_tags:
                t = self.open_tags.pop()
                self.out.append("</%s>" % t)
                if t == tag:
                    break

    def handle_data(self, data):
        if not self.drop_depth:
            self.out.append(html_mod.escape(data, False))

    def handle_comment(self, data):
        pass

    def result(self):
        while self.open_tags:
            self.out.append("</%s>" % self.open_tags.pop())
        return "".join(self.out)


def sanitize(fragment, link_resolver=None):
    s = Sanitizer(link_resolver)
    try:
        s.feed(fragment)
        s.close()
    except Exception:
        return html_mod.escape(fragment, False)
    return s.result()


# ---------------------------------------------------------------- 문법 하이라이터
# 의존성 없이 쓰는 토큰 기반 하이라이터. 언어별 규칙을 하나의 마스터 정규식으로 합쳐
# finditer 로 훑고, 결과를 "줄 단위로 닫힌" span 목록으로 만든다 (줄번호 뷰와 호환).
HL_MAX_BYTES = 1024 * 1024        # 이보다 큰 코드는 하이라이팅 생략
HL_MAX_LINES = 20000

_KW = {
    "java": ("abstract assert break case catch class const continue default do else enum extends "
             "final finally for goto if implements import instanceof interface native new package "
             "private protected public return static strictfp super switch synchronized this throw "
             "throws transient try volatile while yield record sealed permits var",
             "boolean byte char double float int long short void String Object Integer Long Double "
             "Boolean List Map Set Optional Stream true false null"),
    "kotlin": ("as break by class companion const constructor continue crossinline data delegate "
               "do dynamic else enum expect external final finally for fun get if import in infix "
               "init inline inner interface internal is lateinit noinline object open operator out "
               "override package private protected public reified return sealed set super suspend "
               "tailrec this throw try typealias val var vararg when where while",
               "Any Boolean Byte Char Double Float Int Long Nothing Short String Unit List Map Set "
               "MutableList MutableMap true false null it"),
    "js": ("async await break case catch class const continue debugger default delete do else "
           "export extends finally for from function get if import in instanceof let new of "
           "return set static super switch this throw try typeof var void while with yield",
           "Array Boolean Date Error JSON Map Math Number Object Promise RegExp Set String Symbol "
           "console document window undefined true false null NaN Infinity"),
    "ts": ("abstract any as asserts async await break case catch class const constructor continue "
           "declare default delete do else enum export extends finally for from function get "
           "implements import in infer instanceof interface is keyof let module namespace never "
           "new of private protected public readonly return satisfies set static super switch this "
           "throw try type typeof unique unknown var void while yield",
           "Array Boolean Date Error JSON Map Number Object Promise Record RegExp Set String "
           "boolean number string symbol object bigint undefined true false null NaN"),
    "python": ("and as assert async await break class continue def del elif else except finally "
               "for from global if import in is lambda match nonlocal not or pass raise return try "
               "while with yield",
               "abs all any bool bytes dict enumerate filter float format frozenset getattr "
               "hasattr int isinstance len list map max min next object open print range repr "
               "reversed round self set setattr sorted str sum super tuple type zip True False "
               "None Exception ValueError TypeError KeyError"),
    "go": ("break case chan const continue default defer else fallthrough for func go goto if "
           "import interface map package range return select struct switch type var",
           "bool byte complex64 complex128 error float32 float64 int int8 int16 int32 int64 rune "
           "string uint uint8 uint16 uint32 uint64 uintptr append cap close copy delete len make "
           "new panic print println recover true false nil iota"),
    "rust": ("as async await break const continue crate dyn else enum extern fn for if impl in "
             "let loop match mod move mut pub ref return self static struct super trait type "
             "unsafe use where while",
             "bool char f32 f64 i8 i16 i32 i64 i128 isize str u8 u16 u32 u64 u128 usize String "
             "Vec Option Result Box Some None Ok Err true false"),
    "c": ("auto break case const continue default do double else enum extern float for goto if "
          "inline int long register restrict return short signed sizeof static struct switch "
          "typedef union unsigned void volatile while",
          "bool char size_t int8_t int16_t int32_t int64_t uint8_t uint32_t uint64_t FILE NULL "
          "true false"),
    "cpp": ("alignas alignof asm auto bool break case catch class const constexpr const_cast "
            "continue decltype default delete do double dynamic_cast else enum explicit export "
            "extern false final float for friend goto if inline int long mutable namespace new "
            "noexcept nullptr operator override private protected public register "
            "reinterpret_cast return short signed sizeof static static_assert static_cast struct "
            "switch template this thread_local throw true try typedef typeid typename union "
            "unsigned using virtual void volatile while",
            "char size_t string vector map set unique_ptr shared_ptr ostream istream std NULL"),
    "cs": ("abstract as async await base break case catch checked class const continue decimal "
           "default delegate do else enum event explicit extern finally fixed for foreach get "
           "goto if implicit in interface internal is lock namespace new null operator out "
           "override params partial private protected public readonly record ref return sealed "
           "set sizeof stackalloc static struct switch this throw try typeof unchecked unsafe "
           "using var virtual void volatile where while yield",
           "bool byte char decimal double dynamic float int long object sbyte short string uint "
           "ulong ushort List Dictionary Task IEnumerable true false"),
    "swift": ("as associatedtype async await break case catch class continue default defer deinit "
              "do else enum extension fallthrough fileprivate final for func guard if import in "
              "init inout internal is lazy let mutating nil open operator override private "
              "protocol public repeat required return self static struct subscript super switch "
              "throw throws try typealias var weak where while",
              "Any Array Bool Character Dictionary Double Float Int Optional Set String UInt Void "
              "true false nil self"),
    "php": ("abstract and array as break callable case catch class clone const continue declare "
            "default do echo else elseif empty enddeclare endfor endforeach endif endswitch "
            "endwhile enum extends final finally fn for foreach function global goto if implements "
            "include include_once instanceof insteadof interface isset list match namespace new or "
            "print private protected public readonly require require_once return static switch "
            "throw trait try unset use var while xor yield",
            "array bool float int object string void null true false self parent"),
    "ruby": ("alias and begin break case class def defined? do else elsif end ensure false for if "
             "in module next nil not or redo rescue retry return self super then true undef unless "
             "until when while yield",
             "attr_accessor attr_reader attr_writer include extend require require_relative puts "
             "print lambda proc new nil true false"),
    "sql": ("select insert update delete from where group by order having limit offset join inner "
            "left right full outer on as and or not in exists between like is null distinct union "
            "all create alter drop table view index primary key foreign references default values "
            "set into case when then else end asc desc count sum avg min max cast with recursive "
            "begin commit rollback transaction procedure function trigger returning using "
            "constraint unique check add column modify rename truncate grant revoke",
            "int integer bigint smallint decimal numeric float double char varchar nvarchar text "
            "clob blob date time datetime timestamp boolean serial uuid json jsonb"),
    "shell": ("if then else elif fi for while until do done case esac in function select time "
              "break continue return exit local export readonly declare typeset unset shift trap "
              "source eval exec set",
              "echo printf cd pwd ls cp mv rm mkdir rmdir touch cat head tail grep sed awk sort "
              "uniq wc cut tr find xargs curl wget git docker python python3 pip npm make sudo "
              "chmod chown kill ps true false test"),
    "groovy": ("as assert break case catch class def do else enum extends final finally for goto "
               "if implements import in instanceof interface new package return static super "
               "switch this throw throws trait try while",
               "boolean byte char double float int long short void String List Map Set Closure "
               "true false null it task plugins dependencies apply"),
    "dart": ("abstract as assert async await break case catch class const continue covariant "
             "default deferred do dynamic else enum export extends extension external factory "
             "false final finally for get if implements import in interface is late library mixin "
             "new null on operator part required rethrow return set show static super switch sync "
             "this throw true try typedef var void while with yield",
             "bool double int num String List Map Set Future Stream Widget BuildContext"),
    "lua": ("and break do else elseif end false for function goto if in local nil not or repeat "
            "return then true until while",
            "print pairs ipairs type tostring tonumber table string math os io require"),
}
_KW["javascript"] = _KW["mjs"] = _KW["cjs"] = _KW["jsx"] = _KW["js"]
_KW["typescript"] = _KW["tsx"] = _KW["ts"]
_KW["py"] = _KW["python"]
_KW["cc"] = _KW["hpp"] = _KW["cxx"] = _KW["cpp"]
_KW["h"] = _KW["c"]
_KW["csharp"] = _KW["cs"]
_KW["kt"] = _KW["kts"] = _KW["kotlin"]
_KW["rs"] = _KW["rust"]
_KW["rb"] = _KW["ruby"]
_KW["bash"] = _KW["sh"] = _KW["zsh"] = _KW["shell"]
_KW["gradle"] = _KW["groovy"]
_KW["mysql"] = _KW["psql"] = _KW["plsql"] = _KW["sql"]

# 공통 조각
_NUM = r"\b(?:0[xX][0-9a-fA-F_]+|0[bB][01_]+|\d[\d_]*(?:\.[\d_]+)?(?:[eE][+-]?\d+)?)[lLfFdDuU]*\b"
_STR_D = r'"(?:\\.|[^"\\\n])*"?'
_STR_S = r"'(?:\\.|[^'\\\n])*'?"
_STR_BT = r"`(?:\\.|[^`\\])*`?"
_TRIPLE = r'"""[\s\S]*?(?:"""|\Z)|\'\'\'[\s\S]*?(?:\'\'\'|\Z)'


def _kwrx(words):
    return r"\b(?:%s)\b" % "|".join(sorted((w for w in words.split() if w), key=len, reverse=True))


def _build(rules):
    """[(cls, pattern)] -> (compiled, {groupname: cls})"""
    names, parts = {}, []
    for idx, (cls, pat) in enumerate(rules):
        nm = "g%d" % idx
        names[nm] = cls
        parts.append("(?P<%s>%s)" % (nm, pat))
    return re.compile("|".join(parts), re.M), names


def _generic_rules(lang, line_comments=("//",), block=(("/*", "*/"),), triple=False,
                   backtick=False, annotation=r"@\w+", flags_i=False):
    kw, ty = _KW.get(lang, ("", ""))
    rules = []
    for a, b in block:
        rules.append(("c", r"%s[\s\S]*?(?:%s|\Z)" % (re.escape(a), re.escape(b))))
    for lc in line_comments:
        rules.append(("c", r"%s[^\n]*" % re.escape(lc)))
    if triple:
        rules.append(("s", _TRIPLE))
    if backtick:
        rules.append(("s", _STR_BT))
    rules += [("s", _STR_D), ("s", _STR_S), ("n", _NUM)]
    if annotation:
        rules.append(("v", annotation))
    if kw:
        rules.append(("k", _kwrx(kw)))
    if ty:
        rules.append(("t", _kwrx(ty)))
    rules.append(("f", r"\b[A-Za-z_]\w*(?=\s*\()"))
    return rules


def _markup_rules():
    return [
        ("c", r"<!--[\s\S]*?(?:-->|\Z)"),
        ("c", r"<!\[CDATA\[[\s\S]*?(?:\]\]>|\Z)"),
        ("v", r"(?i)<!doctype[^>]*>"),
        ("g", r"</?[A-Za-z][\w:.-]*"),
        ("g", r"/?>"),
        ("a", r"\b[A-Za-z_:][\w:.-]*(?=\s*=)"),
        ("s", _STR_D), ("s", _STR_S),
    ]


_CSS_PSEUDO = ("root hover focus focus-visible focus-within active visited link target empty "
               "enabled disabled checked indeterminate required optional read-only read-write "
               "first-child last-child only-child first-of-type last-of-type only-of-type "
               "nth-child nth-last-child nth-of-type nth-last-of-type not is where has any-link "
               "default valid invalid in-range out-of-range placeholder-shown before after "
               "first-line first-letter selection placeholder marker backdrop "
               "file-selector-button part slotted host host-context dir lang")


def _css_rules():
    return [
        ("c", r"/\*[\s\S]*?(?:\*/|\Z)"),
        ("s", _STR_D), ("s", _STR_S),
        ("v", r"--[\w-]+|\$[\w-]+|@[\w-]+"),
        ("f", r"\b[a-zA-Z-]+(?=\()"),
        ("a", r"[\w-]+(?=\s*:)"),
        ("n", r"#[0-9a-fA-F]{3,8}\b|\b\d+(?:\.\d+)?(?:px|em|rem|%|vh|vw|s|ms|fr|deg|pt)?\b"),
        ("t", r"[.#][\w-]+|::?(?:%s)\b"
              % "|".join(sorted(_CSS_PSEUDO.split(), key=len, reverse=True))),
    ]


def _json_rules():
    return [
        ("a", r'"(?:\\.|[^"\\])*"(?=\s*:)'),
        ("s", _STR_D),
        ("n", r"-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b"),
        ("k", r"\b(?:true|false|null)\b"),
        ("c", r"//[^\n]*|/\*[\s\S]*?(?:\*/|\Z)"),
    ]


def _yaml_rules():
    return [
        ("c", r"#[^\n]*"),
        ("v", r"(?:(?<=^)|(?<=\s))-(?=\s)"),
        ("a", r"(?:(?<=^)|(?<=[\s-]))[\w.$/-]+(?=\s*:)"),
        ("s", _STR_D), ("s", _STR_S),
        ("k", r"\b(?:true|false|null|yes|no|on|off|~)\b"),
        ("n", _NUM),
        ("v", r"[&*][\w-]+|<<:|\|\-?|>\-?"),
    ]


def _ini_rules():
    return [
        ("c", r"[#;][^\n]*"),
        ("t", r"(?:(?<=^)|(?<=[\s]))\[[^\]\n]*\]"),
        ("a", r"(?:(?<=^)|(?<=[\s]))[\w.$-]+(?=\s*[:=])"),
        ("s", _STR_D), ("s", _STR_S),
        ("v", r"\$\{[^}\n]*\}|\$\w+"),
        ("k", r"\b(?:true|false|null|on|off|yes|no)\b"),
        ("n", _NUM),
    ]


def _shell_rules():
    kw, ty = _KW["shell"]
    return [
        ("c", r"#[^\n]*"),
        ("s", r"<<-?\s*'?(\w+)'?[\s\S]*?^\s*\1"),
        ("s", _STR_D), ("s", _STR_S),
        ("v", r"\$\{[^}\n]*\}|\$[\w@*#?!$-]+"),
        ("k", _kwrx(kw)),
        ("t", _kwrx(ty)),
        ("a", r"(?<=\s)--?[\w-]+"),
        ("n", _NUM),
    ]


def _diff_rules():
    return [
        ("dm", r"^(?:diff|index|---|\+\+\+|old mode|new mode|similarity|rename)[^\n]*"),
        ("dh", r"^@@[^\n]*"),
        ("da", r"^\+[^\n]*"),
        ("dd", r"^-[^\n]*"),
        ("c", r"^\\[^\n]*"),
    ]


_RULESETS = {}


def _ruleset(lang):
    """언어별 마스터 정규식 (캐시)."""
    if lang in _RULESETS:
        return _RULESETS[lang]
    if lang in ("html", "htm", "xhtml", "xml", "svg", "vue", "svelte", "jsp", "thymeleaf"):
        rules = _markup_rules()
    elif lang in ("css", "scss", "sass", "less"):
        rules = _css_rules()
    elif lang in ("json", "json5", "jsonc"):
        rules = _json_rules()
    elif lang in ("yaml", "yml"):
        rules = _yaml_rules()
    elif lang in ("ini", "cfg", "conf", "properties", "toml", "env", "editorconfig", "gitconfig"):
        rules = _ini_rules()
    elif lang in ("sh", "bash", "zsh", "shell", "fish", "ksh"):
        rules = _shell_rules()
    elif lang in ("diff", "patch"):
        rules = _diff_rules()
    elif lang in ("python", "py", "pyi"):
        rules = _generic_rules("python", line_comments=("#",), block=(), triple=True,
                               annotation=r"@[\w.]+|\bf(?=[\"'])")
    elif lang in ("ruby", "rb", "perl", "pl", "r", "jl", "tcl", "awk"):
        rules = _generic_rules(lang if lang in _KW else "ruby", line_comments=("#",), block=(),
                               annotation=r"[@$]\w+|:\w+")
    elif lang in ("sql", "mysql", "psql", "plsql", "pgsql"):
        kw, ty = _KW["sql"]
        rules = [("c", r"/\*[\s\S]*?(?:\*/|\Z)"), ("c", r"--[^\n]*"), ("c", r"#[^\n]*"),
                 ("s", _STR_S), ("s", _STR_D), ("n", _NUM),
                 ("k", r"(?i)" + _kwrx(kw)), ("t", r"(?i)" + _kwrx(ty)),
                 ("v", r"[@:#]\w+|\?"), ("f", r"\b[A-Za-z_]\w*(?=\s*\()")]
    elif lang in ("lua",):
        rules = _generic_rules("lua", line_comments=("--",), block=(("--[[", "]]"),))
    elif lang in ("vim",):
        rules = _generic_rules("vim", line_comments=('"',), block=())
    elif lang in ("go", "rust", "rs", "swift", "dart", "scala"):
        rules = _generic_rules(lang, triple=(lang in ("swift", "scala")))
    elif lang in _KW:
        rules = _generic_rules(lang, backtick=lang in ("js", "javascript", "ts", "typescript",
                                                       "jsx", "tsx", "mjs", "cjs", "kotlin",
                                                       "kt", "kts", "groovy", "gradle"),
                               triple=lang in ("kotlin", "kt", "kts", "groovy", "gradle",
                                               "java", "cs", "csharp"))
    else:
        _RULESETS[lang] = None
        return None
    built = _build(rules)
    _RULESETS[lang] = built
    return built


LANG_ALIAS = {
    "c++": "cpp", "objective-c": "c", "objc": "c", "m": "c", "mm": "cpp", "cs": "cs",
    "c#": "cs", "golang": "go", "node": "js", "text": "", "txt": "", "plain": "",
    "console": "shell", "terminal": "shell", "shell-session": "shell", "dockerfile": "shell",
    "makefile": "shell", "make": "shell", "gitignore": "ini", "log": "",
}


def norm_lang(lang):
    lang = (lang or "").strip().lower().lstrip(".")
    return LANG_ALIAS.get(lang, lang)


def highlight_lines(code, lang):
    """코드를 '줄 단위로 완결된 HTML' 리스트로 변환. 하이라이팅 불가 시 이스케이프만."""
    code = code.replace("\r\n", "\n").replace("\r", "\n")
    plain = [html_mod.escape(ln, False) for ln in code.split("\n")]
    lang = norm_lang(lang)
    if not lang or len(code) > HL_MAX_BYTES or len(plain) > HL_MAX_LINES:
        return plain, False
    built = _ruleset(lang)
    if not built:
        return plain, False
    rx, names = built
    try:
        tokens, pos = [], 0
        for m in rx.finditer(code):
            if m.start() > pos:
                tokens.append((None, code[pos:m.start()]))
            tokens.append((names.get(m.lastgroup), m.group(0)))
            pos = m.end()
        if pos < len(code):
            tokens.append((None, code[pos:]))
    except Exception:
        return plain, False

    lines, cur = [], []
    for cls, text in tokens:
        chunks = text.split("\n")
        for k, chunk in enumerate(chunks):
            if k:
                lines.append("".join(cur))
                cur = []
            if chunk:
                esc = html_mod.escape(chunk, False)
                cur.append('<span class="hl-%s">%s</span>' % (cls, esc) if cls else esc)
    lines.append("".join(cur))
    if len(lines) != len(plain):      # 안전장치: 줄 수가 어긋나면 원본 사용
        return plain, False
    return lines, True


def highlight_block(code, lang):
    lines, ok = highlight_lines(code, lang)
    return "\n".join(lines), ok


# ---------------------------------------------------------------- 마크다운 렌더러
FENCE_RE = re.compile(r"^(\s{0,3})(`{3,}|~{3,})\s*([^`\s]*)\s*$")
ATX_RE = re.compile(r"^(\s{0,3})(#{1,6})\s+(.*?)\s*#*\s*$")
HR_RE = re.compile(r"^\s{0,3}(?:(?:\*\s*){3,}|(?:-\s*){3,}|(?:_\s*){3,})$")
LIST_RE = re.compile(r"^(\s*)([-*+]|\d{1,9}[.)])(\s+)(.*)$")
QUOTE_RE = re.compile(r"^\s{0,3}>[ \t]?(.*)$")
SETEXT_RE = re.compile(r"^\s{0,3}(=+|-+)\s*$")
TABLE_DELIM_RE = re.compile(r"^\s{0,3}\|?(?:\s*:?-+:?\s*\|)+\s*:?-*:?\s*\|?\s*$")
LINKDEF_RE = re.compile(r"^\s{0,3}\[([^\]]+)\]:\s*<?([^\s>]+)>?(?:\s+[\"'(](.*)[\"')])?\s*$")
BLOCK_HTML_TAGS = ("div", "table", "thead", "tbody", "tr", "td", "th", "ul", "ol", "li", "dl",
                   "dt", "dd", "p", "blockquote", "pre", "details", "summary", "figure", "center",
                   "h1", "h2", "h3", "h4", "h5", "h6", "hr", "br", "img", "section", "article")
ALERT_RE = re.compile(r"^\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\]\s*$", re.I)

PH_OPEN = "\x02"
PH_CLOSE = "\x03"
PH_RE = re.compile(PH_OPEN + r"(\d+)" + PH_CLOSE)
ENTITY_RE = re.compile(r"&(?:#[0-9]{1,7}|#[xX][0-9a-fA-F]{1,6}|[a-zA-Z][a-zA-Z0-9]{1,31});")
COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")
COMMENT_OPEN_RE = re.compile(r"^\s{0,3}<!--")


class _InlineTag(Sanitizer):
    """조각 단위 raw HTML 태그 살균 (자동 닫기 없음)."""

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in DROP_CONTENT_TAGS:
            if self.drop_depth:
                self.drop_depth -= 1
            return
        if self.drop_depth or tag not in ALLOWED_TAGS or tag in VOID_TAGS:
            return
        self.out.append("</%s>" % tag)

    def result(self):
        return "".join(self.out)


def sanitize_tag(token, resolve=None):
    p = _InlineTag(resolve)
    try:
        p.feed(token)
        p.close()
    except Exception:
        return html_mod.escape(token, False)
    r = p.result()
    return r if r else html_mod.escape(token, False)


class Markdown(object):
    """GFM 부분집합 렌더러: 제목/코드펜스/목록(중첩,체크박스)/표/인용/링크정의/각종 인라인."""

    def __init__(self, link_resolver=None, unsafe=False):
        self.resolve = link_resolver
        self.unsafe = unsafe
        self.mermaid = True
        self.toc = []
        self._slugs = {}
        self._ph = []
        self._defs = {}

    # ---- placeholder ----
    def _stash(self, s):
        self._ph.append(s)
        return "%s%d%s" % (PH_OPEN, len(self._ph) - 1, PH_CLOSE)

    def _unstash(self, s):
        for _ in range(6):
            if PH_OPEN not in s:
                break
            s = PH_RE.sub(lambda m: self._ph[int(m.group(1))], s)
        return s

    # ---- 링크 ----
    def _link(self, url, is_img):
        url = safe_url(url, allow_data_image=is_img)
        if url is None:
            return None, {}
        if self.resolve:
            return self.resolve(url, is_img)
        return url, {}

    def _slug(self, text):
        t = re.sub(r"<[^>]+>", "", text)
        t = html_mod.unescape(self._unstash(t)).strip().lower()
        t = re.sub(r"[^\w\s-]", "", t, flags=re.UNICODE)
        t = re.sub(r"[\s_]+", "-", t).strip("-")
        t = t or "section"
        n = self._slugs.get(t, 0)
        self._slugs[t] = n + 1
        return t if n == 0 else "%s-%d" % (t, n)

    # ---- 인라인 ----
    def inline(self, text):
        # 1) 코드 스팬
        def code_span(m):
            body = m.group(2)
            if body.startswith(" ") and body.endswith(" ") and body.strip():
                body = body[1:-1]
            return self._stash("<code>%s</code>" % html_mod.escape(body, False))
        text = re.sub(r"(?<!\\)(`+)([\s\S]*?[^`]|)\1(?!`)", code_span, text)

        # 1.5) HTML 주석 제거 (코드 스팬은 이미 stash 되어 안전)
        text = COMMENT_RE.sub("", text)

        # 2) raw HTML 태그
        if self.unsafe:
            text = re.sub(r"</?[a-zA-Z][^<>]*>", lambda m: self._stash(m.group(0)), text)
        else:
            text = re.sub(r"</?[a-zA-Z][^<>]*>",
                          lambda m: self._stash(sanitize_tag(m.group(0), self.resolve)), text)

        # 3) 백슬래시 이스케이프
        text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!>~|=&])",
                      lambda m: self._stash(html_mod.escape(m.group(1), False)), text)

        # 3.5) HTML 엔티티 참조(&nbsp; &#160; &#x2192; ...)는 문자로 치환해 그대로 통과
        text = ENTITY_RE.sub(
            lambda m: self._stash(html_mod.escape(html_mod.unescape(m.group(0)), False)), text)

        # 4) 엔티티 이스케이프
        text = html_mod.escape(text, False)

        # 5) 이미지
        def img(m):
            alt, url, title = m.group(1), m.group(2).strip("<>"), m.group(3)
            u, _extra = self._link(html_mod.unescape(url), True)
            if u is None:
                return html_mod.escape(m.group(0), False)
            t = ' title="%s"' % html_mod.escape(title, True) if title else ""
            return self._stash('<img src="%s" alt="%s"%s loading="lazy">'
                               % (html_mod.escape(u, True), html_mod.escape(alt, True), t))
        text = re.sub(r"!\[([^\]]*)\]\(\s*([^\s)]*)(?:\s+[\"'](.*?)[\"'])?\s*\)", img, text)

        # 6) 링크 [text](url) / [text][ref] / [ref]
        def link(m):
            label, url, title = m.group(1), m.group(2).strip("<>"), m.group(3)
            u, extra = self._link(html_mod.unescape(url), False)
            if u is None:
                return html_mod.escape(m.group(0), False)
            return self._render_a(u, label, title, extra)
        text = re.sub(r"\[((?:[^\[\]]|\[[^\]]*\])*)\]\(\s*([^\s)]*)(?:\s+[\"'](.*?)[\"'])?\s*\)",
                      link, text)

        def refl(m):
            label = m.group(1)
            key = (m.group(2) or label).strip().lower()
            d = self._defs.get(key)
            if not d:
                return m.group(0)
            u, extra = self._link(d[0], False)
            if u is None:
                return m.group(0)
            return self._render_a(u, label, d[1], extra)
        text = re.sub(r"\[((?:[^\[\]])+)\](?:\[([^\]]*)\])?", refl, text)

        # 7) autolink / bare URL
        text = re.sub(r"&lt;((?:https?|mailto):[^\s&]+)&gt;",
                      lambda m: self._stash('<a href="%s" target="_blank" rel="noreferrer">%s</a>'
                                            % (html_mod.escape(m.group(1), True), m.group(1))), text)
        text = re.sub(r"(?<![\"'>=/\w])(https?://[^\s<>\"')\]]+[^\s<>\"')\].,;:!?])",
                      lambda m: self._stash('<a href="%s" target="_blank" rel="noreferrer">%s</a>'
                                            % (html_mod.escape(m.group(1), True), m.group(1))), text)

        # 8) 강조
        text = re.sub(r"\*\*\*(\S(?:[\s\S]*?\S)?)\*\*\*", r"<strong><em>\1</em></strong>", text)
        text = re.sub(r"\*\*(\S(?:[\s\S]*?\S)?)\*\*", r"<strong>\1</strong>", text)
        text = re.sub(r"(?<![\w])__(\S(?:[\s\S]*?\S)?)__(?![\w])", r"<strong>\1</strong>", text)
        text = re.sub(r"(?<!\*)\*(?!\s)(\S(?:[\s\S]*?\S)?)\*(?!\*)", r"<em>\1</em>", text)
        text = re.sub(r"(?<![\w_])_(?!\s)(\S(?:[\s\S]*?\S)?)_(?![\w_])", r"<em>\1</em>", text)
        text = re.sub(r"~~(\S(?:[\s\S]*?\S)?)~~", r"<del>\1</del>", text)
        text = re.sub(r"==(\S(?:[\s\S]*?\S)?)==", r"<mark>\1</mark>", text)

        # 9) 줄바꿈
        text = re.sub(r"(  +|\\)\n", "<br>\n", text)
        return self._unstash(text)

    def _render_a(self, u, label, title, extra):
        t = ' title="%s"' % html_mod.escape(title, True) if title else ""
        at = "".join(' %s="%s"' % (k, html_mod.escape(v, True)) for k, v in extra.items())
        if re.match(r"^(https?|mailto):", u, re.I):
            at += ' target="_blank" rel="noreferrer"'
        return self._stash('<a href="%s"%s%s>%s</a>'
                           % (html_mod.escape(u, True), t, at, self.inline(label)))

    # ---- 블록 ----
    def render(self, text):
        text = text.replace("\r\n", "\n").replace("\r", "\n").expandtabs(4)
        lines = text.split("\n")
        keep = []
        for ln in lines:                     # 링크 정의 수집 (줄번호는 유지)
            m = LINKDEF_RE.match(ln)
            if m:
                self._defs[m.group(1).strip().lower()] = (m.group(2), m.group(3) or "")
                keep.append("")
            else:
                keep.append(ln)
        body = self._blocks(keep, base_line=0)
        return body, self.toc

    def _is_block_start(self, line):
        if not line.strip():
            return True
        return bool(FENCE_RE.match(line) or ATX_RE.match(line) or HR_RE.match(line)
                    or LIST_RE.match(line) or QUOTE_RE.match(line)
                    or COMMENT_OPEN_RE.match(line)
                    or re.match(r"^\s{0,3}<(?:%s)[\s/>]" % "|".join(BLOCK_HTML_TAGS), line, re.I))

    @staticmethod
    def _with_line(chunk, no):
        """블록 HTML 의 첫 태그에 data-line 을 붙인다 (소스<->미리보기 동기화용)."""
        m = re.match(r"<([a-zA-Z][\w-]*)", chunk)
        if not m:
            return chunk
        return "%s data-line=\"%d\"%s" % (chunk[:m.end()], no, chunk[m.end():])

    def _blocks(self, lines, in_list=False, base_line=None):
        out = []
        i, n = 0, len(lines)
        marked, block_start = 0, 0
        while i < n:
            if base_line is not None and len(out) > marked:
                for k in range(marked, len(out)):
                    out[k] = self._with_line(out[k], base_line + block_start + 1)
                marked = len(out)
            block_start = i
            line = lines[i]
            if not line.strip():
                i += 1
                continue

            m = FENCE_RE.match(line)
            if m:
                ch, opener, info = m.group(2)[0], m.group(2), (m.group(3) or "")
                indent = len(m.group(1))
                close = re.compile(r"^\s{0,3}%s{%d,}\s*$" % (re.escape(ch), len(opener)))
                i += 1
                body = []
                while i < n and not close.match(lines[i]):
                    ln = lines[i]
                    body.append(ln[indent:] if ln[:indent].strip() == "" else ln.lstrip())
                    i += 1
                i += 1 if i < n else 0
                lang = re.sub(r"[^\w.+#-]", "", info)[:24]
                if self.mermaid and lang.lower() == "mermaid":
                    # 그리는 건 브라우저 몫이다. 스크립트가 없거나 문법이 틀리면
                    # 여기 담긴 원문이 그대로 코드 블록처럼 남는다.
                    out.append('<pre class="mermaid" data-mermaid="1"><code>%s</code>'
                               '</pre>' % html_mod.escape("\n".join(body), False))
                    continue
                code, hl_ok = highlight_block("\n".join(body), lang)
                out.append('<pre class="code%s"%s><code%s>%s</code></pre>' % (
                    " hl" if hl_ok else "",
                    ' data-lang="%s"' % html_mod.escape(lang, True) if lang else "",
                    ' class="language-%s"' % html_mod.escape(lang, True) if lang else "",
                    code))
                continue

            m = ATX_RE.match(line)
            if m:
                level = len(m.group(2))
                inner = self.inline(m.group(3))
                sid = self._slug(m.group(3))
                self.toc.append({"level": level, "id": sid,
                                 "text": re.sub(r"<[^>]+>", "", inner)})
                out.append('<h%d id="%s">%s<a class="hanchor" href="#%s">#</a></h%d>'
                           % (level, sid, inner, sid, level))
                i += 1
                continue

            if HR_RE.match(line):
                out.append("<hr>")
                i += 1
                continue

            # HTML 주석 블록 -> 출력하지 않는다 (닫는 --> 가 없으면 일반 문단으로)
            if COMMENT_OPEN_RE.match(line):
                j = i
                while j < n and "-->" not in lines[j]:
                    j += 1
                if j < n:
                    i = j + 1
                    continue

            # 4칸 들여쓰기 코드 블록 (목록 안에서는 오탐이 많아 제외)
            if not in_list and re.match(r"^ {4,}\S", line):
                body = []
                while i < n and (re.match(r"^ {4}", lines[i]) or not lines[i].strip()):
                    body.append(lines[i][4:])
                    i += 1
                while body and not body[-1].strip():
                    body.pop()
                out.append('<pre class="code"><code>%s</code></pre>'
                           % html_mod.escape("\n".join(body), False))
                continue

            if QUOTE_RE.match(line):
                buf = []
                while i < n and lines[i].strip():
                    mm = QUOTE_RE.match(lines[i])
                    buf.append(mm.group(1) if mm else lines[i].strip())
                    i += 1
                cls, title = "quote", None
                if buf:
                    am = ALERT_RE.match(buf[0].strip())
                    if am:
                        cls = "quote alert alert-%s" % am.group(1).lower()
                        title = am.group(1).upper()
                        buf = buf[1:]
                inner = self._blocks(buf)
                head = '<div class="alert-title">%s</div>' % title if title else ""
                out.append('<blockquote class="%s">%s%s</blockquote>' % (cls, head, inner))
                continue

            if "|" in line and i + 1 < n and TABLE_DELIM_RE.match(lines[i + 1]):
                out.append(self._table(lines, i))
                while i < n and lines[i].strip() and ("|" in lines[i]):
                    i += 1
                continue

            m = LIST_RE.match(line)
            if m and not HR_RE.match(line):
                blk, i = self._collect_list(lines, i)
                out.append(blk)
                continue

            if re.match(r"^\s{0,3}<(?:%s)[\s/>]" % "|".join(BLOCK_HTML_TAGS), line, re.I):
                buf = []
                while i < n and lines[i].strip():
                    buf.append(lines[i])
                    i += 1
                chunk = "\n".join(buf)
                out.append(chunk if self.unsafe else sanitize(chunk, self.resolve))
                continue

            # 문단 (setext 제목 포함)
            buf = [line]
            i += 1
            while i < n and lines[i].strip() and not self._is_block_start(lines[i]):
                sm = SETEXT_RE.match(lines[i])
                if sm:
                    break
                buf.append(lines[i])
                i += 1
            if i < n and SETEXT_RE.match(lines[i]) and len(buf) >= 1:
                level = 1 if lines[i].strip().startswith("=") else 2
                raw = " ".join(x.strip() for x in buf)
                inner = self.inline(raw)
                sid = self._slug(raw)
                self.toc.append({"level": level, "id": sid, "text": re.sub(r"<[^>]+>", "", inner)})
                out.append('<h%d id="%s">%s<a class="hanchor" href="#%s">#</a></h%d>'
                           % (level, sid, inner, sid, level))
                i += 1
                continue
            out.append("<p>%s</p>" % self.inline("\n".join(buf)))
        if base_line is not None and len(out) > marked:
            for k in range(marked, len(out)):
                out[k] = self._with_line(out[k], base_line + block_start + 1)
        return "\n".join(out)

    def _table(self, lines, i):
        def cells(row):
            row = row.strip()
            if row.startswith("|"):
                row = row[1:]
            if row.endswith("|") and not row.endswith("\\|"):
                row = row[:-1]
            return [c.strip() for c in re.split(r"(?<!\\)\|", row)]

        header = cells(lines[i])
        aligns = []
        for c in cells(lines[i + 1]):
            left, right = c.startswith(":"), c.endswith(":")
            aligns.append("center" if left and right else "right" if right
                          else "left" if left else "")
        rows = []
        j = i + 2
        while j < len(lines) and lines[j].strip() and "|" in lines[j]:
            rows.append(cells(lines[j]))
            j += 1

        def td(tag, val, idx):
            a = aligns[idx] if idx < len(aligns) else ""
            style = ' style="text-align:%s"' % a if a else ""
            return "<%s%s>%s</%s>" % (tag, style, self.inline(val.replace("\\|", "|")), tag)

        out = ['<div class="table-wrap"><table><thead><tr>']
        out += [td("th", c, k) for k, c in enumerate(header)]
        out.append("</tr></thead><tbody>")
        for r in rows:
            out.append("<tr>")
            out += [td("td", c, k) for k, c in enumerate(r[:len(header)] if header else r)]
            out.append("</tr>")
        out.append("</tbody></table></div>")
        return "".join(out)

    def _collect_list(self, lines, start):
        """같은 레벨 목록 블록 하나를 통째로 처리 (중첩은 재귀)."""
        m = LIST_RE.match(lines[start])
        base_indent = len(m.group(1))
        ordered = not m.group(2)[0] in "-*+"
        first_num = re.sub(r"\D", "", m.group(2)) if ordered else ""
        items, cur = [], None
        i, n = start, len(lines)
        loose = False
        blanks = 0
        while i < n:
            line = lines[i]
            if not line.strip():
                blanks += 1
                if cur is not None:
                    cur.append("")
                i += 1
                continue
            mm = LIST_RE.match(line)
            indent = len(line) - len(line.lstrip())
            if mm and indent <= base_indent + 1:
                same_type = (not mm.group(2)[0] in "-*+") == ordered
                if not same_type and indent <= base_indent:
                    break
                if cur is not None:
                    if blanks:
                        loose = True
                    items.append(cur)
                cur = [mm.group(4)]
                cur_meta = indent + len(mm.group(2)) + len(mm.group(3))
                blanks = 0
                i += 1
                # 항목 본문 (더 깊게 들여쓴 줄들)
                while i < n:
                    ln = lines[i]
                    if not ln.strip():
                        nxt = lines[i + 1] if i + 1 < n else ""
                        if nxt.strip() and (len(nxt) - len(nxt.lstrip())) < max(cur_meta - 1, base_indent + 1):
                            break
                        cur.append("")
                        i += 1
                        continue
                    ind = len(ln) - len(ln.lstrip())
                    if ind <= base_indent + 1 and LIST_RE.match(ln):
                        break
                    if ind < max(cur_meta - 1, base_indent + 2) and not LIST_RE.match(ln):
                        if ind <= base_indent:
                            break
                    cur.append(ln[min(ind, cur_meta):] if ind >= cur_meta else ln.strip())
                    i += 1
                continue
            if indent <= base_indent:
                break
            if cur is not None:
                cur.append(line.strip())
            i += 1
        if cur is not None:
            items.append(cur)

        html_items = []
        for item in items:
            while item and not item[-1].strip():
                item.pop()
            body = self._blocks(item, in_list=True).strip()
            if not loose:      # 타이트 목록: 첫 문단의 <p> 래퍼 제거
                body = re.sub(r"^<p>([\s\S]*?)</p>", r"\1", body, count=1)
            task = ""
            tm = re.match(r"^(<p>)?\s*\[([ xX])\]\s+", body)
            if tm:
                on = " on" if tm.group(2).lower() == "x" else ""
                body = (tm.group(1) or "") + body[tm.end():]
                task = ' class="task"'
                body = '<span class="chk%s"></span>' % on + body
            html_items.append("<li%s>%s</li>" % (task, body))
        tag = "ol" if ordered else "ul"
        attr = ""
        if ordered and first_num and first_num != "1":
            attr = ' start="%s"' % first_num
        return ("<%s%s%s>%s</%s>" % (tag, attr, ' class="loose"' if loose else "",
                                     "".join(html_items), tag), i)


def render_markdown(text, link_resolver=None, unsafe=False):
    md = Markdown(link_resolver, unsafe)
    md.mermaid = CFG.mermaid
    body, toc = md.render(text)
    return body, toc


# ---------------------------------------------------------------- URL/라우팅 헬퍼
def q(s):
    return urllib.parse.quote(s, safe="/")


def raw_url(rid, rel):
    return "/f/%s/%s" % (rid, q(rel))


def hash_route(rid, rel):
    return "#fs/%s/%s" % (rid, q(rel))


def fs_link_resolver(rid, dir_rel):
    """md 안의 상대 링크를 뷰어 라우트/파일 URL 로 바꿔준다."""
    def resolve(url, is_img):
        if not url or url.startswith("#") or url.startswith("//"):
            return url, {}
        if re.match(r"^[a-zA-Z][\w+.-]*:", url):
            return url, {}
        path, _, frag = url.partition("#")
        path = path.split("?")[0]
        if not path:
            return url, {}
        rel = urllib.parse.unquote(path)
        base = "" if rel.startswith("/") else dir_rel
        target = posixpath.normpath(posixpath.join(base, rel.lstrip("/")))
        if target.startswith("..") or target == ".":
            return url, {}
        if is_img:
            return raw_url(rid, target), {}
        route = hash_route(rid, target)
        if frag:
            route += "?h=" + q(frag)
        return route, {"data-dv": "1"}
    return resolve


# ---------------------------------------------------------------- 파일 트리 / 검색
def entry_info(rid, p, root):
    try:
        st = p.stat()
    except OSError:
        return None
    is_dir = p.is_dir()
    rel = rel_of(rid, p)
    remote = bool(root and root.get("cloud") and not is_dir and not materialized(st))
    return {
        "remote": remote,
        "name": p.name,
        "root": rid,
        "path": rel,
        "dir": is_dir,
        "kind": "dir" if is_dir else kind_of(p),
        "size": 0 if is_dir else st.st_size,
        "sizeText": "" if is_dir else fmt_size(st.st_size),
        "mtime": int(st.st_mtime),
    }


def list_dir(rid, rel, show_hidden=False):
    p = safe_path(rid, rel)
    if not p.is_dir():
        raise NotADirectoryError(rel)
    root = root_by_id(rid)
    dirs, files = [], []
    try:
        it = sorted(p.iterdir(), key=lambda x: x.name.lower())
    except PermissionError:
        it = []
    for child in it:
        nm = child.name
        if nm == ".DS_Store":
            continue
        if not show_hidden and nm.startswith("."):
            continue
        if child.is_dir() and nm in SKIP_DIRS:
            continue
        info = entry_info(rid, child, root)
        if info is None:
            continue
        (dirs if info["dir"] else files).append(info)
    return dirs + files


def walk_files(rid, rel, show_hidden=False, deadline=None):
    base = safe_path(rid, rel)
    for dirpath, dirnames, filenames in os.walk(str(base)):
        if deadline and time.time() > deadline:
            return
        dirnames[:] = sorted(d for d in dirnames
                             if d not in SKIP_DIRS and (show_hidden or not d.startswith(".")))
        for fn in sorted(filenames):
            if fn == ".DS_Store" or (not show_hidden and fn.startswith(".")):
                continue
            if deadline and time.time() > deadline:
                return
            yield Path(dirpath) / fn


def search(query, rid=None, limit=200, budget=6.0):
    """파일명 + 내용 검색. 여러 루트를 라운드로빈으로 훑어 한 루트가 상한을 독식하지 않게 한다.

    클라우드 placeholder(아직 안 내려온 파일)는 내용 검색에서 제외하고 파일명만 본다.
    """
    needle = query.strip()
    if not needle:
        return [], False, 0
    low = needle.lower()
    deadline = time.time() + budget
    roots = [r for r in CFG.roots if (rid in (None, "", "all") or r["id"] == rid)]
    if not roots:
        return [], False, 0

    def scan(root):
        """루트 하나를 훑는 제너레이터 (매치 1건씩 yield)."""
        for p in walk_files(root["id"], "", CFG.show_hidden, deadline):
            name_hit = low in p.name.lower()
            lines = []
            skipped_here = 0
            if searchable(p):
                try:
                    st = p.stat()
                    if root.get("lazy") and not materialized(st):
                        skipped_here = 1
                    elif st.st_size <= MAX_SEARCH_BYTES:
                        text, _tr = read_text(p, MAX_SEARCH_BYTES)
                        for no, line in enumerate(text.split("\n"), 1):
                            if low in line.lower():
                                lines.append({"no": no, "text": line.strip()[:240]})
                                if len(lines) >= 5:
                                    break
                except OSError:
                    pass
            if name_hit or lines:
                yield ({
                    "root": root["id"], "rootName": root["name"],
                    "path": rel_of(root["id"], p), "name": p.name,
                    "kind": kind_of(p), "nameHit": name_hit, "lines": lines,
                }, skipped_here)
            elif skipped_here:
                yield (None, skipped_here)

    gens = [scan(r) for r in roots]
    results, skipped, hit_limit = [], 0, False
    while gens and len(results) < limit:
        if time.time() > deadline:
            hit_limit = True
            break
        for g in list(gens):
            if time.time() > deadline:
                hit_limit = True
                gens = []
                break
            try:
                item, sk = next(g)
            except StopIteration:
                gens.remove(g)
                continue
            skipped += sk
            if item:
                results.append(item)
            if len(results) >= limit:
                hit_limit = True
                break
    if (gens and len(results) >= limit) or time.time() > deadline:
        hit_limit = True          # 상한/시간예산에 걸렸으면 UI 에 "일부 생략" 표시
    results.sort(key=lambda r: (0 if r["nameHit"] else 1, len(r["path"])))
    return results, hit_limit, skipped


# ---------------------------------------------------------------- CSV / office
def csv_to_html(text, delim=None):
    if delim is None:
        delim = "\t" if text.count("\t") > text.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), delimiter=delim))
    if not rows:
        return "<p class='muted'>빈 파일</p>"
    head, body = rows[0], rows[1:]
    out = ['<div class="table-wrap"><table><thead><tr data-line="1">']
    out += ["<th>%s</th>" % html_mod.escape(c or "", False) for c in head]
    out.append("</tr></thead><tbody>")
    for idx, r in enumerate(body[:5000]):
        out.append('<tr data-line="%d">' % (idx + 2)
                   + "".join("<td>%s</td>" % html_mod.escape(c or "", False) for c in r)
                   + "</tr>")
    out.append("</tbody></table></div>")
    if len(body) > 5000:
        out.append("<p class='muted'>%d 행 중 5000 행만 표시</p>" % len(body))
    return "".join(out)


def find_soffice():
    cand = [shutil.which("soffice"), shutil.which("libreoffice"),
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
            "/usr/bin/soffice", "/usr/lib/libreoffice/program/soffice"]
    for c in cand:
        if c and os.path.exists(c):
            return c
    return None


_office_lock = threading.Lock()


# StarCalc HTML 필터는 시트마다 이런 머리말을 넣는다:
#   <A NAME="table3"><h1>Sheet 4: <em>nvmessage</em></h1></A>
_SHEET_RE = re.compile(
    r'<A NAME="(table\d+)"><h1>Sheet\s*\d+:\s*<em>(.*?)</em></h1></A>', re.I | re.S)


def office_sheets(outdir, html_name):
    """변환된 시트 문서에서 [{"id": 앵커, "name": 시트명}, ...] 를 뽑는다.

    LibreOffice 는 시트를 전부 한 파일에 이어 붙인다. 문서 맨 위에 Overview 링크가
    붙긴 하지만, 시트 수십 장짜리 7MB 문서에서 시트를 옮기려고 매번 맨 위까지
    스크롤해 올라가는 건 못 쓸 물건이다. 목록만 뽑아서 뷰어 툴바에 고정해 둔다.

    수 MB 짜리 파일을 열 때마다 정규식으로 훑지 않도록 결과를 캐시 폴더에 남긴다.
    """
    cache = outdir / "_sheets.json"
    try:
        if cache.is_file():
            return json.loads(cache.read_text("utf-8"))
    except Exception:
        pass
    try:
        html = (outdir / html_name).read_text("utf-8", errors="replace")
    except Exception:
        return []
    sheets = [{"id": a, "name": html_mod.unescape(re.sub(r"<[^>]+>", "", n)).strip()}
              for a, n in _SHEET_RE.findall(html)]
    try:
        cache.write_text(json.dumps(sheets, ensure_ascii=False), "utf-8")
    except Exception:
        pass
    return sheets


def soffice_env():
    """soffice 에 넘길 환경변수. fonts.conf 가 있으면 폰트 대체 규칙을 적용한다.

    문서가 '맑은 고딕' 같은 윈도우 전용 한글 폰트를 지정했는데 이 머신에 그 폰트가
    없으면, LibreOffice 는 자기가 번들한 폰트에서 대체품을 고른다. 그런데 번들 폰트에는
    한글이 하나도 없어서 히브리어 폰트(FrankRuhlHofshi) 같은 걸 집어오고, 그 결과
    한글이 통째로 사라진다. fonts.conf 가 시스템에 실제로 있는 CJK 폰트를 찍어준다.
    """
    env = dict(os.environ)
    conf = HOME / "fonts.conf"
    if not conf.is_file():
        conf = BASE / "fonts.conf"
    if conf.is_file():
        env["FONTCONFIG_FILE"] = str(conf)
    return env


def office_convert(path):
    """soffice 로 office 문서를 변환하고 (캐시키, 파일명) 반환.

    문서 종류마다 목표 포맷이 다르다.
      - 워드(docx/doc/rtf/odt) -> html : "HTML (StarWriter)" 필터. 표·이미지가 살고
                                         화면 폭에 맞춰 리플로우되므로 읽기 좋다
      - 시트(xlsx/xls/ods)     -> html : "HTML (StarCalc)" 필터. 표로 잘 떨어진다.
                                         넓은 시트를 pdf 로 뽑으면 열이 페이지마다
                                         잘려서 오히려 못 본다
      - 슬라이드(pptx/ppt/odp) -> pdf

    슬라이드를 html 로 뽑으면 LibreOffice 가 odf2xhtml(XSLT) 필터로 떨어지는데,
    이건 장표 경계 없이 전체를 한 문서로 이어 붙이고 도형·이미지도 대부분 잃는다
    (17장짜리 발표자료가 그림 12개만 남은 2MB 짜리 한 페이지가 된다).
    대안인 impress_html_Export 는 반대로 텍스트 개요만 남기고 그림을 전부 버린다.
    pdf 는 장표당 1페이지에 원본 레이아웃이 그대로 남고, 뷰어가 이미 pdf 를
    iframe 으로 인라인 렌더하므로 프론트는 손댈 것이 없다.
    """
    if not CFG.soffice:
        return None
    fmt = "pdf" if path.suffix.lower() in SLIDE_EXT else "html"
    st = path.stat()
    # 캐시키에 fmt 를 넣어야 규칙이 바뀌었을 때 예전 결과가 재사용되지 않는다
    key = hashlib.sha1(("%s|%s|%s|%s" % (path, st.st_mtime, st.st_size, fmt))
                       .encode()).hexdigest()[:16]
    outdir = CACHE / "office" / key
    with _office_lock:
        existing = sorted(outdir.glob("*." + fmt)) if outdir.exists() else []
        if existing:
            return key, existing[0].name
        outdir.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run([CFG.soffice, "--headless", "--norestore", "--convert-to", fmt,
                            "--outdir", str(outdir), str(path)],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=120,
                           env=soffice_env(), check=False)
        except Exception:
            return None
        existing = sorted(outdir.glob("*." + fmt))
        return (key, existing[0].name) if existing else None


# ---------------------------------------------------------------- 문서 빌더
def build_doc(rid, rel):
    p = safe_path(rid, rel)
    if not p.exists():
        raise FileNotFoundError(rel)
    root = root_by_id(rid)
    if p.is_dir():
        return {"kind": "dir", "root": rid, "rootName": root["name"], "path": rel,
                "name": p.name or root["name"], "entries": list_dir(rid, rel, CFG.show_hidden)}
    st = p.stat()
    kind = kind_of(p)
    remote = bool(root.get("cloud") and not materialized(st))
    doc = {
        "remote": remote,
        "kind": kind, "root": rid, "rootName": root["name"], "path": rel, "name": p.name,
        "absPath": str(p), "size": st.st_size, "sizeText": fmt_size(st.st_size),
        "mtime": int(st.st_mtime), "rawUrl": raw_url(rid, rel), "toc": [],
    }
    dir_rel = posixpath.dirname(rel)
    if kind == "md":
        text, truncated = read_text(p)
        body, toc = render_markdown(text, fs_link_resolver(rid, dir_rel), CFG.md_unsafe)
        doc["html"] = body
        doc["toc"] = toc
        doc["sourceLang"] = "markdown"
        doc["truncated"] = truncated
        doc["source"] = text if len(text) < 512 * 1024 else ""   # 원본 보기용 (큰 문서는 생략)
    elif kind == "html":
        text, truncated = read_text(p)
        doc["source"] = text
        doc["truncated"] = truncated
    elif kind == "csv":
        text, truncated = read_text(p)
        doc["sourceLang"] = p.suffix.lower().lstrip(".")
        doc["html"] = csv_to_html(text, "\t" if p.suffix.lower() == ".tsv" else None)
        doc["source"] = text
        doc["truncated"] = truncated
    elif kind == "text":
        text, truncated = read_text(p)
        lang = p.suffix.lower().lstrip(".") or p.name.lower()
        doc["content"] = text
        doc["lang"] = lang
        doc["truncated"] = truncated
        lines, hl_ok = highlight_lines(text, lang)
        if hl_ok:
            doc["lines"] = lines
            doc["hlLang"] = norm_lang(lang)
    elif kind == "gstub":
        info = read_gstub(p)
        doc.update(info)
        doc["name"] = p.stem or p.name
        if info["driveId"] and DRIVE.status()["authed"]:
            try:                        # 인증돼 있으면 내용까지 가져와 인라인 렌더
                inline = drive_doc(info["driveId"])
                for k in ("kind", "html", "toc", "source", "content", "lang", "mime",
                          "rawUrl", "size", "sizeText"):
                    if k in inline:
                        doc[k] = inline[k]
                doc["viaStub"] = True
            except Exception as e:
                doc["driveError"] = "%s: %s" % (type(e).__name__, e)
        elif info["driveId"]:
            doc["driveError"] = "인라인으로 보려면 Drive 연결이 필요합니다."
    elif kind == "office":
        conv = office_convert(p)
        if conv:
            doc["officeUrl"] = "/office/%s/%s" % (conv[0], q(conv[1]))
            if p.suffix.lower() in SHEET_EXT:
                doc["sheets"] = office_sheets(CACHE / "office" / conv[0], conv[1])
        else:
            doc["officeError"] = ("LibreOffice(soffice) 가 없어 변환할 수 없습니다."
                                  if not CFG.soffice else "변환에 실패했습니다.")
    return doc


# ---------------------------------------------------------------- Google Drive
GD_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
GD_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GD_TOKEN = "https://oauth2.googleapis.com/token"
GD_API = "https://www.googleapis.com/drive/v3"
GD_FIELDS = ("files(id,name,mimeType,modifiedTime,size,iconLink,webViewLink,shortcutDetails),"
             "nextPageToken")
GD_EXPORT = {
    "application/vnd.google-apps.document": ("text/markdown", "md"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", "csv"),
    "application/vnd.google-apps.presentation": ("application/pdf", "pdf"),
    "application/vnd.google-apps.drawing": ("image/png", "png"),
    "application/vnd.google-apps.script": ("application/vnd.google-apps.script+json", "json"),
}
ID_RE = re.compile(r"(?:/folders/|/d/|[?&]id=)([A-Za-z0-9_-]{10,})")


def http_json(url, data=None, headers=None, method=None):
    body = None
    hdr = {"Accept": "application/json"}
    if headers:
        hdr.update(headers)
    if isinstance(data, dict):
        body = urllib.parse.urlencode(data).encode()
        hdr["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data=body, headers=hdr, method=method)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


class Drive(object):
    """표준 라이브러리만 쓰는 최소 Drive v3 클라이언트 (읽기 전용)."""

    def __init__(self):
        self.client_file = HOME / "gdrive_client.json"
        self.token_file = HOME / "gdrive_token.json"
        self._cache = {}
        self.lock = threading.Lock()

    # ---- 설정/토큰 ----
    def client(self):
        try:
            d = json.loads(self.client_file.read_text("utf-8"))
        except Exception:
            return None
        d = d.get("installed") or d.get("web") or d
        if d.get("client_id"):
            return d
        return None

    def tok(self):
        try:
            return json.loads(self.token_file.read_text("utf-8"))
        except Exception:
            return {}

    def save_tok(self, t):
        HOME.mkdir(parents=True, exist_ok=True)
        self.token_file.write_text(json.dumps(t, indent=2), "utf-8")
        try:
            os.chmod(str(self.token_file), 0o600)
        except OSError:
            pass

    def status(self):
        c = self.client()
        return {"configured": bool(c), "authed": bool(self.tok().get("refresh_token")),
                "clientFile": str(self.client_file)}

    def auth_url(self, redirect_uri, state):
        c = self.client()
        if not c:
            return None
        params = {
            "client_id": c["client_id"], "redirect_uri": redirect_uri, "response_type": "code",
            "scope": GD_SCOPE, "access_type": "offline", "prompt": "consent",
            "include_granted_scopes": "true", "state": state,
        }
        return GD_AUTH + "?" + urllib.parse.urlencode(params)

    def exchange(self, code, redirect_uri):
        c = self.client()
        r = http_json(GD_TOKEN, data={
            "code": code, "client_id": c["client_id"],
            "client_secret": c.get("client_secret", ""),
            "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
        r["expires_at"] = time.time() + float(r.get("expires_in", 3000)) - 60
        old = self.tok()
        if not r.get("refresh_token") and old.get("refresh_token"):
            r["refresh_token"] = old["refresh_token"]
        self.save_tok(r)
        return r

    def access_token(self):
        with self.lock:
            t = self.tok()
            if t.get("access_token") and float(t.get("expires_at", 0)) > time.time():
                return t["access_token"]
            if not t.get("refresh_token"):
                raise PermissionError("Drive 인증이 필요합니다.")
            c = self.client()
            r = http_json(GD_TOKEN, data={
                "refresh_token": t["refresh_token"], "client_id": c["client_id"],
                "client_secret": c.get("client_secret", ""), "grant_type": "refresh_token"})
            t.update(r)
            t["expires_at"] = time.time() + float(r.get("expires_in", 3000)) - 60
            self.save_tok(t)
            return t["access_token"]

    def _auth_hdr(self):
        return {"Authorization": "Bearer " + self.access_token()}

    # ---- API ----
    def list(self, folder_id="root", page_token=None):
        params = {
            "q": "'%s' in parents and trashed = false" % folder_id.replace("'", ""),
            "fields": GD_FIELDS, "pageSize": "200",
            "orderBy": "folder,name", "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true",
        }
        if page_token:
            params["pageToken"] = page_token
        return http_json(GD_API + "/files?" + urllib.parse.urlencode(params),
                         headers=self._auth_hdr())

    def meta(self, file_id):
        params = {"fields": "id,name,mimeType,modifiedTime,size,webViewLink,parents,"
                            "shortcutDetails", "supportsAllDrives": "true"}
        return http_json(GD_API + "/files/%s?%s" % (file_id, urllib.parse.urlencode(params)),
                         headers=self._auth_hdr())

    def search(self, query):
        safe = query.replace("'", "").replace("\\", "")
        params = {"q": "fullText contains '%s' and trashed = false" % safe,
                  "fields": GD_FIELDS, "pageSize": "50", "supportsAllDrives": "true",
                  "includeItemsFromAllDrives": "true"}
        return http_json(GD_API + "/files?" + urllib.parse.urlencode(params),
                         headers=self._auth_hdr())

    def download(self, file_id):
        """(bytes, mime, meta) — 구글 문서면 export 로 변환해서 가져온다."""
        ck = "dl:" + file_id
        hit = self._cache.get(ck)
        if hit and hit[0] > time.time():
            return hit[1]
        m = self.meta(file_id)
        mime = m.get("mimeType", "")
        if mime == "application/vnd.google-apps.shortcut":
            tgt = (m.get("shortcutDetails") or {}).get("targetId")
            if tgt:
                return self.download(tgt)
        if mime.startswith("application/vnd.google-apps."):
            exp = GD_EXPORT.get(mime)
            if not exp:
                raise ValueError("미리보기를 지원하지 않는 형식입니다: " + mime)
            url = GD_API + "/files/%s/export?mimeType=%s" % (file_id, q(exp[0]))
            out_mime = exp[0]
        else:
            url = GD_API + "/files/%s?alt=media&supportsAllDrives=true" % file_id
            out_mime = mime
        req = urllib.request.Request(url, headers=self._auth_hdr())
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
            out_mime = r.headers.get("Content-Type", out_mime).split(";")[0] or out_mime
        res = (data, out_mime, m)
        if len(data) < 8 * 1024 * 1024:
            self._cache[ck] = (time.time() + 300, res)
            if len(self._cache) > 64:
                self._cache.clear()
        return res


DRIVE = Drive()


def drive_doc(file_id):
    data, mime, meta = DRIVE.download(file_id)
    name = meta.get("name", file_id)
    doc = {"kind": "binary", "name": name, "driveId": file_id, "mime": mime,
           "webViewLink": meta.get("webViewLink", ""), "toc": [],
           "size": len(data), "sizeText": fmt_size(len(data)),
           "rawUrl": "/api/gdrive/raw?id=" + q(file_id)}
    ext = posixpath.splitext(name)[1].lower()
    text = None
    if mime.startswith("text/") or mime in ("application/json", "application/xml") \
            or ext in MD_EXT | HTML_EXT | TEXT_EXT | CSV_EXT:
        for enc in ("utf-8", "utf-8-sig", "cp949", "euc-kr"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    if mime in ("text/markdown", "text/x-markdown") or ext in MD_EXT:
        body, toc = render_markdown(text or "", None, CFG.md_unsafe)
        doc.update(kind="md", html=body, toc=toc, source=text or "")
    elif mime == "text/csv" or ext in CSV_EXT:
        doc.update(kind="csv", html=csv_to_html(text or ""), source=text or "")
    elif mime == "text/html" or ext in HTML_EXT:
        doc.update(kind="html", source=text or "")
    elif mime == "application/pdf" or ext == ".pdf":
        doc.update(kind="pdf")
    elif mime.startswith("image/"):
        doc.update(kind="image")
    elif text is not None:
        doc.update(kind="text", content=text, lang=ext.lstrip(".") or "txt")
    return doc


# ---------------------------------------------------------------- 편집 미리보기 버퍼
PREVIEW_TTL = 600
PREVIEW_MAX = 8
_preview_buf = {}
_preview_lock = threading.Lock()


def preview_put(html_text, base_url):
    """편집 중인 HTML 을 메모리에 잠깐 보관하고 조회용 id 를 돌려준다."""
    pid = secrets.token_urlsafe(9)
    now = time.time()
    with _preview_lock:
        for k in [k for k, v in _preview_buf.items() if v[0] < now]:
            _preview_buf.pop(k, None)
        while len(_preview_buf) >= PREVIEW_MAX:
            _preview_buf.pop(next(iter(_preview_buf)), None)
        _preview_buf[pid] = (now + PREVIEW_TTL, html_text, base_url)
    return pid


def preview_get(pid):
    with _preview_lock:
        item = _preview_buf.get(pid)
    if not item or item[0] < time.time():
        return None
    return item[1], item[2]


def inject_base(html_text, base_url):
    """상대 경로(이미지/CSS)가 원본 폴더 기준으로 풀리도록 <base> 를 넣는다."""
    tag = '<base href="%s">' % html_mod.escape(base_url, True)
    m = re.search(r"<head[^>]*>", html_text, re.I)
    if m:
        return html_text[:m.end()] + tag + html_text[m.end():]
    return tag + html_text


# ---------------------------------------------------------------- Host 검증
LOOPBACK_NAMES = {"127.0.0.1", "localhost", "::1", "0:0:0:0:0:0:0:1"}
_host_cache = {}
_host_cache_lock = threading.Lock()


def _is_local_ip(name):
    """이 머신의 인터페이스에 실제로 존재하는 IP 인지 bind 로 확인 (캐시)."""
    if not re.match(r"^[0-9a-f.:]+$", name):
        return False
    with _host_cache_lock:
        if name in _host_cache:
            return _host_cache[name]
    ok = False
    for fam in (socket.AF_INET, socket.AF_INET6):
        s = socket.socket(fam, socket.SOCK_STREAM)
        try:
            s.bind((name, 0))
            ok = True
        except OSError:
            pass
        finally:
            s.close()
        if ok:
            break
    with _host_cache_lock:
        _host_cache[name] = ok
    return ok


def host_allowed(name):
    """DNS rebinding 차단: 루프백 이름, 내 호스트명, 그리고 (LAN 노출 시) 내 IP 만 허용.

    공격자는 자기 도메인이 우리 IP 로 해석되게 만들어 접근하므로 Host 는 '도메인 이름'이
    된다. 아래 규칙은 그런 이름을 전부 거부한다.
    """
    name = (name or "").strip().lower().rstrip(".")
    if name in LOOPBACK_NAMES:
        return True
    if name in CFG.host_names:
        return True
    if CFG.lan and _is_local_ip(name):
        return True
    return False


# ------------------------------------------------------------ mermaid 스크립트
_mermaid_lock = threading.Lock()
_mermaid_err = [""]      # 한 번 실패하면 기억한다 (요청마다 CDN 을 두드리지 않게)


def _mermaid_local():
    """이미 갖고 있는 mermaid 스크립트 경로 (없으면 None)."""
    for p in (ASSETS / MERMAID_FILE, ASSETS / "mermaid.min.js", CACHE / MERMAID_FILE):
        try:
            if p.is_file() and p.stat().st_size >= MERMAID_MIN_BYTES:
                return p
        except OSError:
            pass
    return None


def mermaid_script():
    """(경로, "") 또는 (None, 사유). 없으면 CDN 에서 한 번만 내려받아 cache/ 에 둔다.

    문서를 CDN 으로 새어나가게 하지 않으려고 스크립트도 우리 서버가 대신 준다
    (페이지 CSP 가 script-src 'self' 이기도 하다). 오프라인이면 사유를 돌려주고,
    뷰어는 다이어그램 자리에 원문과 그 사유를 보여준다.
    """
    p = _mermaid_local()
    if p:
        return p, ""
    with _mermaid_lock:
        p = _mermaid_local()          # 락을 기다리는 사이 다른 요청이 받았을 수 있다
        if p:
            return p, ""
        if _mermaid_err[0]:
            return None, _mermaid_err[0]
        try:
            req = urllib.request.Request(MERMAID_URL, headers={"User-Agent": APP})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = r.read()
            if len(data) < MERMAID_MIN_BYTES or b"mermaid" not in data[:4096]:
                raise ValueError("내려받은 파일이 mermaid 가 아닙니다 (%d bytes)" % len(data))
            CACHE.mkdir(parents=True, exist_ok=True)
            dst = CACHE / MERMAID_FILE
            tmp = CACHE / (MERMAID_FILE + ".part")
            tmp.write_bytes(data)
            tmp.replace(dst)          # 반쯤 쓰인 파일이 캐시로 보이지 않게 원자적으로
            return dst, ""
        except Exception as e:
            _mermaid_err[0] = "mermaid 스크립트를 받지 못했습니다: %s" % e
            return None, _mermaid_err[0]


# ---------------------------------------------------------------- HTTP 서버
COOKIE = "dvtok"
PAGE_CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
            "font-src 'self' data:; connect-src 'self'; frame-src 'self'; "
            "object-src 'self'; base-uri 'none'; form-action 'none'")
SUBRESOURCE_DESTS = {"image", "font", "style", "script", "media", "object", "embed",
                     "iframe", "frame", "track", "manifest"}


class Handler(BaseHTTPRequestHandler):
    server_version = "docs-viewer/%s" % VERSION
    protocol_version = "HTTP/1.1"

    # ---- 기본 유틸 ----
    def log_message(self, fmt, *args):
        if os.environ.get("DOCS_VIEWER_VERBOSE"):
            sys.stderr.write("  %s\n" % (fmt % args))

    def _host_ok(self):
        raw = (self.headers.get("Host") or "").strip().lower()
        if not raw:
            return False
        # host:port 분리 (IPv6 리터럴 [::1]:8765 형태 포함)
        if raw.startswith("["):
            name = raw.split("]")[0].lstrip("[")
            port = raw.split("]")[-1].lstrip(":")
        else:
            parts = raw.rsplit(":", 1)
            name = parts[0]
            port = parts[1] if len(parts) > 1 else ""
        if port and port != str(CFG.port):
            return False
        return host_allowed(name)

    def _token_ok(self, qs):
        if not CFG.token:            # --no-token: 인증 없이 허용 (Host 검증은 그대로)
            return True
        t = (qs.get("t") or [""])[0]
        if t and secrets.compare_digest(t, CFG.token):
            return True
        raw = self.headers.get("Cookie")
        if raw:
            try:
                c = SimpleCookie(raw)
                if COOKIE in c and secrets.compare_digest(c[COOKIE].value, CFG.token):
                    return True
            except Exception:
                pass
        return False

    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", extra=None, head=False):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if not (extra and "Cache-Control" in extra):
            self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if not head and body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _json(self, obj, code=200, head=False):
        self._send(code, json.dumps(obj, ensure_ascii=False),
                   "application/json; charset=utf-8", head=head)

    def _err(self, code, msg):
        self._json({"error": msg, "code": code}, code)

    # ---- 라우팅 ----
    MAX_POST = 16 * 1024 * 1024

    def do_POST(self):
        """/api/preview (렌더 미리보기), /api/save (파일 저장)."""
        if not self._host_ok():
            self._send(421, "invalid Host header\n")
            return
        # CSRF 방어: 커스텀 헤더는 교차출처에서 preflight 를 강제하므로 통과할 수 없다
        if self.headers.get("X-Docs-Viewer") != "1":
            self._err(403, "missing X-Docs-Viewer header")
            return
        site = (self.headers.get("Sec-Fetch-Site") or "").lower()
        if site and site != "same-origin":
            self._err(403, "cross-site write blocked")
            return
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        qs = urllib.parse.parse_qs(parsed.query)
        if not self._token_ok(qs):
            self._err(403, "forbidden")
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > self.MAX_POST:
            self._err(413, "본문 크기가 올바르지 않습니다: %d" % length)
            return
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception as e:
            self._err(400, "JSON 파싱 실패: %s" % e)
            return
        try:
            if path == "/api/preview":
                self._json(self._preview(data))
            elif path == "/api/save":
                self._json(self._save(data))
            else:
                self._err(404, "not found: %s" % path)
        except PermissionError as e:
            self._err(403, str(e))
        except FileNotFoundError as e:
            self._err(404, "파일을 찾을 수 없습니다: %s" % e)
        except KeyError as e:
            self._err(400, "잘못된 요청: %s" % e)
        except Exception as e:
            self._err(500, "%s: %s" % (type(e).__name__, e))

    def _preview(self, data):
        kind = data.get("kind") or "md"
        text = data.get("text") or ""
        rid = data.get("root") or ""
        rel = data.get("path") or ""
        if len(text) > MAX_TEXT_BYTES:
            raise ValueError("문서가 너무 큽니다")
        if kind == "md":
            resolver = None
            if root_by_id(rid):
                safe_path(rid, rel)            # 경로 검증만
                resolver = fs_link_resolver(rid, posixpath.dirname(rel))
            body, toc = render_markdown(text, resolver, CFG.md_unsafe)
            return {"html": body, "toc": toc}
        if kind == "html":
            base = "/f/%s/%s" % (rid, q(posixpath.dirname(rel)))
            if not base.endswith("/"):
                base += "/"
            pid = preview_put(text, base)
            return {"url": "/preview/%s" % pid, "toc": []}
        if kind == "csv":
            return {"html": csv_to_html(text, "\t" if rel.lower().endswith(".tsv") else None),
                    "toc": []}
        if kind == "text":
            lang = posixpath.splitext(rel)[1].lstrip(".").lower()
            lines, _ok = highlight_lines(text, lang)
            rows = "".join('<div class="ln" data-line="%d"><span class="n">%d</span>'
                           '<span class="l">%s</span></div>'
                           % (i + 1, i + 1, ln or " ")
                           for i, ln in enumerate(lines[:HL_MAX_LINES]))
            return {"html": '<div class="txt">%s</div>' % rows, "toc": []}
        raise ValueError("미리보기를 지원하지 않는 형식입니다: %s" % kind)

    def _save(self, data):
        if not CFG.allow_edit:
            raise PermissionError("편집이 비활성화되어 있습니다 (--allow-edit 로 실행하세요)")
        rid = data.get("root") or ""
        rel = data.get("path") or ""
        content = data.get("content")
        if not isinstance(content, str):
            raise KeyError("content")
        if len(content) > MAX_TEXT_BYTES:
            raise ValueError("내용이 너무 큽니다 (%d bytes)" % len(content))
        p = safe_path(rid, rel)
        if not p.is_file():
            raise FileNotFoundError(rel)
        if kind_of(p) not in ("md", "html", "csv", "text"):
            raise PermissionError("이 형식은 편집할 수 없습니다: %s" % p.name)
        enc = file_encoding(p)
        tmp = p.with_name(p.name + ".dv-tmp")
        try:
            with tmp.open("wb") as fh:         # 원자적 교체: 임시파일 + rename
                fh.write(content.encode(enc, "replace"))
                fh.flush()
                os.fsync(fh.fileno())
            try:
                shutil.copystat(str(p), str(tmp))
            except OSError:
                pass
            os.replace(str(tmp), str(p))
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        st = p.stat()
        return {"ok": True, "mtime": int(st.st_mtime), "size": st.st_size,
                "sizeText": fmt_size(st.st_size), "encoding": enc}

    def do_HEAD(self):
        self._route(head=True)

    def do_GET(self):
        self._route(head=False)

    def _route(self, head=False):
        if not self._host_ok():
            self._send(421, "invalid Host header\n")
            return
        parsed = urllib.parse.urlsplit(self.path)
        path = urllib.parse.unquote(parsed.path)
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/favicon.ico":
            self._send(200, FAVICON, "image/svg+xml", head=head)
            return

        authed = self._token_ok(qs)

        if path == "/" or path == "/index.html":
            if not authed:
                self._send(403, ACCESS_HELP.replace("__PORT__", str(CFG.port)),
                           "text/html; charset=utf-8", head=head)
                return
            if CFG.token and (qs.get("t") or [""])[0]:
                self._send(302, b"", extra={
                    "Location": "/",
                    "Set-Cookie": "%s=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=604800"
                                  % (COOKIE, CFG.token)}, head=head)
                return
            self._send(200, PAGE, "text/html; charset=utf-8",
                       extra={"Content-Security-Policy": PAGE_CSP}, head=head)
            return

        if path.startswith("/oauth/"):
            self._oauth(path, qs, head)
            return

        if path == "/assets/" + MERMAID_FILE:
            dest = (self.headers.get("Sec-Fetch-Dest") or "").lower()
            if not authed and dest not in SUBRESOURCE_DESTS:
                self._send(403, "forbidden\n", head=head)
                return
            self._serve_mermaid(head)
            return

        if path.startswith("/f/"):
            dest = (self.headers.get("Sec-Fetch-Dest") or "").lower()
            if not authed and dest not in SUBRESOURCE_DESTS:
                self._send(403, "forbidden\n", head=head)
                return
            self._serve_file(path[3:], qs, head)
            return

        if not authed:
            self._err(403, "forbidden - 유효한 토큰이 필요합니다.")
            return

        if path.startswith("/preview/"):
            item = preview_get(path[len("/preview/"):])
            if not item:
                self._send(404, "미리보기가 만료되었습니다\n", "text/plain; charset=utf-8",
                           head=head)
                return
            body = inject_base(item[0], item[1])
            self._send(200, body, "text/html; charset=utf-8", extra={
                "Content-Security-Policy": ("sandbox allow-same-origin; script-src 'none'; "
                                            "object-src 'none'")}, head=head)
            return

        if path.startswith("/office/"):
            self._serve_office(path[len("/office/"):], head)
            return

        try:
            if path == "/api/config":
                self._json(self._config(), head=head)
            elif path == "/api/tree":
                rid = (qs.get("root") or [""])[0]
                rel = (qs.get("path") or [""])[0]
                hidden = (qs.get("hidden") or ["0"])[0] == "1" or CFG.show_hidden
                self._json({"root": rid, "path": rel,
                            "entries": list_dir(rid, rel, hidden)}, head=head)
            elif path == "/api/doc":
                rid = (qs.get("root") or [""])[0]
                rel = (qs.get("path") or [""])[0]
                self._json(build_doc(rid, rel), head=head)
            elif path == "/api/stat":
                p = safe_path((qs.get("root") or [""])[0], (qs.get("path") or [""])[0])
                st = p.stat()
                self._json({"mtime": int(st.st_mtime), "size": st.st_size}, head=head)
            elif path == "/api/search":
                res, capped, skipped = search((qs.get("q") or [""])[0],
                                              (qs.get("root") or [""])[0])
                self._json({"results": res, "capped": capped, "skipped": skipped}, head=head)
            elif path == "/api/gdrive/status":
                self._json(DRIVE.status(), head=head)
            elif path == "/api/gdrive/list":
                fid = (qs.get("id") or ["root"])[0] or "root"
                m = ID_RE.search(fid)
                if m:
                    fid = m.group(1)
                r = DRIVE.list(fid)
                self._json({"id": fid, "files": r.get("files", [])}, head=head)
            elif path == "/api/gdrive/search":
                r = DRIVE.search((qs.get("q") or [""])[0])
                self._json({"files": r.get("files", [])}, head=head)
            elif path == "/api/gdrive/doc":
                fid = (qs.get("id") or [""])[0]
                m = ID_RE.search(fid)
                if m:
                    fid = m.group(1)
                self._json(drive_doc(fid), head=head)
            elif path == "/api/gdrive/raw":
                self._gdrive_raw((qs.get("id") or [""])[0], qs, head)
            else:
                self._err(404, "not found: %s" % path)
        except FileNotFoundError as e:
            self._err(404, "파일을 찾을 수 없습니다: %s" % e)
        except (PermissionError,) as e:
            self._err(403, str(e))
        except KeyError as e:
            self._err(400, "잘못된 요청: %s" % e)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:400]
            except Exception:
                pass
            self._err(502, "Google API 오류 %s %s" % (e.code, detail))
        except Exception as e:
            self._err(500, "%s: %s" % (type(e).__name__, e))

    def _config(self):
        return {
            "version": VERSION,
            "roots": [{"id": r["id"], "name": r["name"], "path": str(r["path"]),
                       "cloud": bool(r.get("cloud")), "lazy": bool(r.get("lazy"))}
                      for r in CFG.roots],
            "driveMounts": [str(m) for m in drive_mounts()],
            "drive": DRIVE.status(),
            "driveTab": (DRIVE.status()["configured"] if CFG.drive_tab is None
                         else bool(CFG.drive_tab)),
            "soffice": bool(CFG.soffice),
            "mdUnsafe": CFG.md_unsafe,
            "canEdit": CFG.allow_edit,
            "mermaid": {"on": CFG.mermaid, "ver": MERMAID_VER},
            "showHidden": CFG.show_hidden,
        }

    # ---- mermaid 스크립트 ----
    def _serve_mermaid(self, head):
        if not CFG.mermaid:
            self._send(404, "mermaid 렌더가 꺼져 있습니다\n", head=head)
            return
        p, err = mermaid_script()
        if not p:
            self._send(503, err + "\n", head=head)
            return
        try:
            data = p.read_bytes()
        except OSError as e:
            self._send(503, "mermaid 스크립트를 읽지 못했습니다: %s\n" % e, head=head)
            return
        # URL 에 버전이 박혀 있으니 오래 캐시해도 안전하다 (3MB 를 매번 받지 않게)
        self._send(200, data, "text/javascript; charset=utf-8",
                   extra={"Cache-Control": "public, max-age=604800, immutable"},
                   head=head)

    # ---- 원본 파일 ----
    def _serve_file(self, spec, qs, head):
        rid, _, rel = spec.partition("/")
        try:
            p = safe_path(rid, rel)
        except (KeyError, PermissionError):
            self._send(403, "forbidden\n", head=head)
            return
        if not p.is_file():
            self._send(404, "not found\n", head=head)
            return
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        kind = kind_of(p)
        if kind in ("text", "md", "csv") and not ctype.startswith("text/"):
            ctype = "text/plain; charset=utf-8"
        if ctype in ("text/plain", "text/markdown", "text/csv"):
            ctype += "; charset=utf-8"
        extra = {}
        if kind == "html":
            ctype = "text/html; charset=utf-8"
            if (qs.get("scripts") or ["0"])[0] == "1":
                extra["Content-Security-Policy"] = "sandbox allow-scripts allow-popups"
            else:
                extra["Content-Security-Policy"] = (
                    "sandbox allow-same-origin allow-popups; script-src 'none'; "
                    "object-src 'none'")
        elif ctype.startswith("image/svg"):
            # SVG 는 최상위 문서로 열면 스크립트가 돌 수 있어 격리한다
            extra["Content-Security-Policy"] = "sandbox; script-src 'none'"
        if (qs.get("dl") or ["0"])[0] == "1":
            extra["Content-Disposition"] = 'attachment; filename="%s"' % (
                urllib.parse.quote(p.name))
        self._send_path(p, ctype, extra, head)

    def _serve_office(self, spec, head):
        key, _, fname = spec.partition("/")
        if not re.match(r"^[0-9a-f]{6,32}$", key or ""):
            self._send(403, "forbidden\n", head=head)
            return
        base = (CACHE / "office" / key).resolve()
        target = Path(os.path.realpath(str(base / urllib.parse.unquote(fname))))
        if base not in target.parents and target != base:
            self._send(403, "forbidden\n", head=head)
            return
        if not target.is_file():
            self._send(404, "not found\n", head=head)
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        extra = {}
        if ctype == "text/html":
            ctype = "text/html; charset=utf-8"
            extra["Content-Security-Policy"] = ("sandbox allow-same-origin; script-src 'none'; "
                                                "object-src 'none'")
        self._send_path(target, ctype, extra, head)

    def _send_path(self, p, ctype, extra, head):
        size = p.stat().st_size
        rng = self.headers.get("Range")
        start, end = 0, size - 1
        code = 200
        if rng:
            m = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
            if m:
                if m.group(1):
                    start = int(m.group(1))
                    end = int(m.group(2)) if m.group(2) else size - 1
                elif m.group(2):
                    start = max(0, size - int(m.group(2)))
                end = min(end, size - 1)
                if start > end or start >= size:
                    self._send(416, "range not satisfiable\n")
                    return
                code = 206
        length = end - start + 1
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-cache")
        if code == 206:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if head:
            return
        try:
            with p.open("rb") as fh:
                fh.seek(start)
                left = length
                while left > 0:
                    chunk = fh.read(min(262144, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _gdrive_raw(self, fid, qs, head):
        m = ID_RE.search(fid or "")
        if m:
            fid = m.group(1)
        data, mime, meta = DRIVE.download(fid)
        extra = {}
        if mime == "text/html":
            extra["Content-Security-Policy"] = ("sandbox allow-same-origin; script-src 'none'; "
                                                "object-src 'none'")
        if (qs.get("dl") or ["0"])[0] == "1":
            extra["Content-Disposition"] = 'attachment; filename="%s"' % (
                urllib.parse.quote(meta.get("name", fid)))
        self._send(200, data, mime or "application/octet-stream", extra=extra, head=head)

    # ---- OAuth ----
    def _oauth(self, path, qs, head):
        redirect = "http://127.0.0.1:%d/oauth/callback" % CFG.port
        if path == "/oauth/start":
            if not self._token_ok(qs):
                self._send(403, "forbidden\n", head=head)
                return
            url = DRIVE.auth_url(redirect, CFG.oauth_state)
            if not url:
                self._send(400, "gdrive_client.json 이 없습니다: %s\n" % DRIVE.client_file,
                           head=head)
                return
            self._send(302, b"", extra={"Location": url}, head=head)
            return
        if path == "/oauth/callback":
            if (qs.get("state") or [""])[0] != CFG.oauth_state:
                self._send(403, "state mismatch\n", head=head)
                return
            if qs.get("error"):
                self._send(400, "인증 거부: %s\n" % qs["error"][0], head=head)
                return
            code = (qs.get("code") or [""])[0]
            try:
                DRIVE.exchange(code, redirect)
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:500]
                self._send(502, "토큰 교환 실패 %s\n%s\n" % (e.code, body), head=head)
                return
            self._send(200, OAUTH_DONE, "text/html; charset=utf-8", head=head)
            return
        self._send(404, "not found\n", head=head)


FAVICON = (b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
           b'<rect width="32" height="32" rx="7" fill="#2b6cb0"/>'
           b'<g fill="#fff"><rect x="8" y="8" width="16" height="2.6" rx="1.3"/>'
           b'<rect x="8" y="14" width="16" height="2.6" rx="1.3"/>'
           b'<rect x="8" y="20" width="10" height="2.6" rx="1.3"/></g></svg>')

ACCESS_HELP = """<!doctype html><meta charset="utf-8">
<title>docs viewer - 토큰 필요</title>
<style>body{font:14px/1.7 -apple-system,"Apple SD Gothic Neo",sans-serif;background:#1e2227;
color:#d7dde4;padding:40px;max-width:640px;margin:auto}code{background:#2c333b;padding:2px 6px;
border-radius:4px}</style>
<h2>토큰이 필요합니다</h2>
<p>이 뷰어는 실행할 때마다 랜덤 토큰을 발급합니다.
터미널에 출력된 <code>http://127.0.0.1:__PORT__/?t=&lt;token&gt;</code> 주소로 접속하세요.</p>
<p>한 번 접속하면 쿠키가 설정되어 이후에는 <code>http://127.0.0.1:__PORT__/</code> 로
바로 들어갈 수 있습니다.</p>
"""

OAUTH_DONE = """<!doctype html><meta charset="utf-8">
<title>Drive 연결 완료</title>
<style>body{font:14px/1.7 -apple-system,"Apple SD Gothic Neo",sans-serif;background:#1e2227;
color:#d7dde4;padding:40px;text-align:center}a{color:#6aa2e8}</style>
<h2>Google Drive 연결 완료</h2>
<p>이 창을 닫고 뷰어로 돌아가세요. <a href="/">뷰어 열기</a></p>
<script>setTimeout(function(){ try{ window.close(); }catch(e){} }, 1200);</script>
"""


# ---------------------------------------------------------------- 프론트엔드
PAGE = r"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>docs viewer</title>
<link rel="icon" href="/favicon.ico">
<style>
:root{
  --bg:#f7f8fa; --panel:#fff; --panel2:#f0f2f5; --hover:#e8ebf0; --active:#dce6f7;
  --fg:#1f2430; --dim:#6b7280; --border:#dfe3e8; --accent:#2563eb; --accent-fg:#fff;
  --code-bg:#f3f4f6; --mark:#fde68a; --shadow:0 6px 24px rgba(20,30,50,.14);
  --ok:#15803d; --warn:#b45309; --err:#b91c1c;
  --hl-c:#8a8f98; --hl-k:#a626a4; --hl-t:#0184bc; --hl-s:#3f8f4a; --hl-n:#b76b01;
  --hl-f:#2f6ef0; --hl-v:#c2410c; --hl-g:#c0392b; --hl-a:#8a6d0b;
  --hl-add:#137333; --hl-add-bg:#e6f4ea; --hl-del:#b3261e; --hl-del-bg:#fce8e6;
  --hl-meta:#6b7280;
}
:root:not([data-theme=light]) { }
@media (prefers-color-scheme: dark){
  :root:not([data-theme=light]){
    --bg:#1a1d21; --panel:#22262b; --panel2:#1f2328; --hover:#2c3238; --active:#2f4368;
    --fg:#dbe1e8; --dim:#8b95a1; --border:#33383f; --accent:#5b93e6; --accent-fg:#fff;
    --code-bg:#282d33; --mark:#7c5c12; --shadow:0 6px 24px rgba(0,0,0,.45);
    --ok:#4ade80; --warn:#fbbf24; --err:#f87171;
    --hl-c:#7f8794; --hl-k:#c678dd; --hl-t:#56b6c2; --hl-s:#98c379; --hl-n:#d19a66;
    --hl-f:#61afef; --hl-v:#e5c07b; --hl-g:#e06c75; --hl-a:#d19a66;
    --hl-add:#7ee787; --hl-add-bg:#132a1b; --hl-del:#ff7b72; --hl-del-bg:#2d1213;
    --hl-meta:#8b95a1;
  }
}
:root[data-theme=dark]{
  --bg:#1a1d21; --panel:#22262b; --panel2:#1f2328; --hover:#2c3238; --active:#2f4368;
  --fg:#dbe1e8; --dim:#8b95a1; --border:#33383f; --accent:#5b93e6; --accent-fg:#fff;
  --code-bg:#282d33; --mark:#7c5c12; --shadow:0 6px 24px rgba(0,0,0,.45);
  --ok:#4ade80; --warn:#fbbf24; --err:#f87171;
  --hl-c:#7f8794; --hl-k:#c678dd; --hl-t:#56b6c2; --hl-s:#98c379; --hl-n:#d19a66;
  --hl-f:#61afef; --hl-v:#e5c07b; --hl-g:#e06c75; --hl-a:#d19a66;
  --hl-add:#7ee787; --hl-add-bg:#132a1b; --hl-del:#ff7b72; --hl-del-bg:#2d1213;
  --hl-meta:#8b95a1;
}
*{box-sizing:border-box}
[hidden]{display:none!important}
html,body{height:100%;margin:0}
body{display:flex;flex-direction:column;background:var(--bg);color:var(--fg);
  font:14px/1.65 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",
  "Noto Sans KR",Segoe UI,sans-serif;-webkit-font-smoothing:antialiased}
button,input,select{font:inherit;color:inherit}
a{color:var(--accent)}
.grow{flex:1 1 auto}
.muted{color:var(--dim)}

header{display:flex;align-items:center;gap:8px;padding:6px 10px;background:var(--panel);
  border-bottom:1px solid var(--border);flex:none;position:relative;z-index:20}
.brand{font-weight:700;letter-spacing:-.2px;font-size:13px;color:var(--dim);flex:none}
.ico{background:none;border:1px solid transparent;border-radius:7px;padding:4px 8px;
  cursor:pointer;line-height:1.1;font-size:14px}
.ico:hover{background:var(--hover)}
.ico.on{background:var(--active);border-color:var(--border)}
.tabs{display:flex;background:var(--panel2);border:1px solid var(--border);border-radius:8px;
  padding:2px;flex:none}
.tab{background:none;border:0;border-radius:6px;padding:3px 12px;cursor:pointer;font-size:12.5px;
  color:var(--dim)}
.tab.on{background:var(--panel);color:var(--fg);font-weight:600;box-shadow:0 1px 2px rgba(0,0,0,.08)}
#crumb{font-size:12px;color:var(--dim);font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:46vw}
#crumb b{color:var(--fg);font-weight:600}
#q{width:min(30vw,300px);background:var(--panel2);border:1px solid var(--border);border-radius:8px;
  padding:5px 10px;outline:none}
#q:focus{border-color:var(--accent);background:var(--panel)}

#wrap{display:flex;flex:1 1 auto;min-height:0}
#side{width:290px;flex:none;background:var(--panel2);border-right:1px solid var(--border);
  display:flex;flex-direction:column;min-height:0}
body.no-side #side,body.no-side #resizer{display:none}
#resizer{flex:none;width:6px;margin-left:-3px;cursor:col-resize;background:transparent;
  position:relative;z-index:10}
#resizer:hover,#resizer.dragging{background:var(--accent);opacity:.35}
body.resizing{cursor:col-resize;user-select:none}
body.resizing iframe{pointer-events:none}
.side-top{padding:8px;display:flex;flex-direction:column;gap:6px;border-bottom:1px solid var(--border)}
.side-row{display:flex;gap:6px;align-items:center}
.side-row .ico{flex:none;border-color:var(--border);color:var(--dim)}
.side-row .ico:hover{color:var(--fg)}
/* 새로고침 중에는 아이콘을 돌린다 (트리 자리에 스피너를 끼우면 화면이 덜컹거린다) */
.ico.busy{pointer-events:none;opacity:.7}
.ico.busy>span{display:inline-block;animation:sp .7s linear infinite}
.side-top select,.side-top input{background:var(--panel);border:1px solid var(--border);
  border-radius:7px;padding:4px 8px;outline:none;width:100%}
.tree{flex:1 1 auto;overflow:auto;min-height:0;padding:6px 4px 12px}
.tree .node{min-width:0}
/* 트리가 길어져도 아래 영역이 찌그러지면 안 된다.
   flex 아이템에 overflow:auto 를 주면 자동 최소높이가 0이 돼서
   shrink 로 눌려버리므로, 여기서는 shrink 를 끄고 안쪽 목록만 스크롤시킨다. */
.side-foot{flex:0 0 auto;min-height:0;max-height:45%;overflow:hidden;
  display:flex;flex-direction:column;
  border-top:1px solid var(--border);padding:2px 6px 8px}
.side-foot details{display:flex;flex-direction:column;min-height:0;flex:0 0 auto}
.side-foot details[open]{flex:1 1 auto}
.side-foot summary{flex:none;cursor:pointer;font-size:12px;color:var(--dim);padding:6px 2px;
  user-select:none}
.side-foot details>div{flex:1 1 auto;min-height:0;overflow:auto}
.node{display:flex;align-items:center;gap:5px;padding:3px 6px;border-radius:6px;cursor:pointer;
  white-space:nowrap;overflow:hidden}
.node:hover{background:var(--hover)}
.node.sel{background:var(--active)}
.node .tw{width:12px;flex:none;text-align:center;color:var(--dim);font-size:10px}
.node .ic{flex:none;width:16px;text-align:center;font-size:12px;opacity:.85}
.node .nm{overflow:hidden;text-overflow:ellipsis}
.node .sz{margin-left:auto;font-size:10.5px;color:var(--dim);flex:none;padding-left:6px}
.cloudmark{opacity:.75}
.node.rootnode{font-weight:700;margin-top:6px;padding:5px 6px;border-radius:7px;
  background:var(--panel);border:1px solid var(--border)}
.node.rootnode:first-child{margin-top:0}
.node.rootnode .nm{overflow:hidden;text-overflow:ellipsis}
.node.hide{display:none}
#tree.filtering .node.fmatch .nm{color:var(--accent)}
.rootkids{margin-left:8px;border-left:1px dotted var(--border)}
.kids{margin-left:12px;border-left:1px dotted var(--border)}
.drive-pane{padding:8px;display:flex;flex-direction:column;gap:8px;min-height:0;flex:1 1 auto}
.drive-pane input{background:var(--panel);border:1px solid var(--border);border-radius:7px;
  padding:5px 8px;outline:none;width:100%}
.btn{background:var(--accent);color:var(--accent-fg);border:0;border-radius:7px;padding:5px 12px;
  cursor:pointer;font-size:12.5px;font-weight:600}
.btn.sec{background:var(--panel);color:var(--fg);border:1px solid var(--border);font-weight:500}
.row{display:flex;gap:6px;align-items:center}

main{flex:1 1 auto;overflow:auto;min-width:0;position:relative}
.doc{max-width:960px;margin:0 auto;padding:26px 34px 120px}
.doc.wide{max-width:none}
/* 기본 960px 은 글 읽기 좋은 한 줄 길이다. 큰 모니터에서 표·다이어그램을 넓게
   보고 싶을 때를 위해 [w] 로 넓게/전체까지 늘린다 (선택은 기억한다). */
body[data-docw="wide"] .doc{max-width:1440px}
body[data-docw="full"] .doc{max-width:none}
#tocbar{width:230px;flex:none;border-left:1px solid var(--border);background:var(--panel2);
  overflow:auto;padding:14px 10px 40px;font-size:12.5px}
body.no-toc #tocbar,#tocbar.empty{display:none}
.toc-h{font-size:11px;text-transform:uppercase;letter-spacing:.08em;color:var(--dim);
  padding:0 6px 8px}
#toc a{display:block;padding:3px 6px;border-radius:5px;color:var(--dim);text-decoration:none;
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#toc a:hover{background:var(--hover);color:var(--fg)}
#toc a.on{color:var(--fg);background:var(--active);font-weight:600}
#toc a[data-l="3"]{padding-left:18px}#toc a[data-l="4"]{padding-left:30px}
#toc a[data-l="5"],#toc a[data-l="6"]{padding-left:40px}

.docbar{display:flex;gap:6px;align-items:center;padding:0 0 14px;flex-wrap:wrap}
.chip{font-size:11.5px;color:var(--dim);background:var(--panel2);border:1px solid var(--border);
  border-radius:999px;padding:2px 10px}
.frame{width:100%;height:calc(100vh - 190px);border:1px solid var(--border);border-radius:10px;
  background:#fff}
.card{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:18px}
.err{color:var(--err)}

/* 마크다운 */
.md{font-size:15px;line-height:1.75;word-break:break-word}
.md h1,.md h2,.md h3,.md h4,.md h5,.md h6{line-height:1.3;margin:1.8em 0 .6em;font-weight:700;
  scroll-margin-top:20px}
.md h1{font-size:1.9em;margin-top:.2em;padding-bottom:.3em;border-bottom:1px solid var(--border)}
.md h2{font-size:1.45em;padding-bottom:.25em;border-bottom:1px solid var(--border)}
.md h3{font-size:1.2em}.md h4{font-size:1.05em}
.md .hanchor{opacity:0;margin-left:.4em;font-size:.7em;text-decoration:none;color:var(--dim)}
.md h1:hover .hanchor,.md h2:hover .hanchor,.md h3:hover .hanchor,.md h4:hover .hanchor{opacity:1}
.md p{margin:.85em 0}
.md ul,.md ol{margin:.7em 0;padding-left:1.6em}
.md li{margin:.25em 0}
.md li>ul,.md li>ol{margin:.2em 0}
.md li>p:first-child{margin-top:0}
.md li>p:last-child{margin-bottom:0}
.md ul.loose>li,.md ol.loose>li{margin:.6em 0}
.md li.task{list-style:none;margin-left:-1.2em}
.md .chk{display:inline-block;width:13px;height:13px;border:1.5px solid var(--dim);
  border-radius:3px;margin-right:7px;vertical-align:-2px}
.md .chk.on{background:var(--accent);border-color:var(--accent);position:relative}
.md .chk.on::after{content:"";position:absolute;left:3px;top:0px;width:4px;height:8px;
  border:solid #fff;border-width:0 2px 2px 0;transform:rotate(40deg)}
.md code{background:var(--code-bg);border-radius:4px;padding:.15em .4em;font-size:.88em;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.md pre.code{background:var(--code-bg);border:1px solid var(--border);border-radius:10px;
  padding:12px 14px;overflow-x:auto;position:relative;margin:1em 0}
.md pre.code code{background:none;padding:0;font-size:12.7px;line-height:1.6;display:block}
.md pre.code[data-lang]::before{content:attr(data-lang);position:absolute;right:10px;top:6px;
  font-size:10px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em}
/* mermaid: 그리기 전(data-mermaid)·실패(mm-err) 는 코드 블록 모양, 그린 뒤엔 그림만 */
.md pre.mermaid{margin:1.1em 0;background:none;border:0;padding:0;text-align:center;
  overflow-x:auto;line-height:normal;font-family:inherit}
.md pre.mermaid[data-mermaid],.md pre.mermaid.mm-err{background:var(--code-bg);
  border:1px solid var(--border);border-radius:10px;padding:12px 14px;text-align:left}
.md pre.mermaid.mm-err{border-color:var(--warn)}
.md pre.mermaid svg{max-width:100%;height:auto}
.md pre.mermaid code{background:none;padding:0;display:block;white-space:pre;
  font-size:12.7px;line-height:1.6;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.md pre.mermaid .mm-msg{margin-bottom:8px;font-size:12px;color:var(--warn);
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.md blockquote{margin:1em 0;padding:.1em 1em;border-left:3px solid var(--border);color:var(--dim)}
.md blockquote.alert{border-left-width:4px;background:var(--panel2);border-radius:0 8px 8px 0;
  padding:.6em 1em;color:var(--fg)}
.md .alert-title{font-weight:700;font-size:12px;letter-spacing:.06em;margin-bottom:.2em}
.md blockquote.alert-note{border-left-color:#3b82f6}.md .alert-note .alert-title{color:#3b82f6}
.md blockquote.alert-tip{border-left-color:var(--ok)}.md .alert-tip .alert-title{color:var(--ok)}
.md blockquote.alert-important{border-left-color:#8b5cf6}
.md .alert-important .alert-title{color:#8b5cf6}
.md blockquote.alert-warning{border-left-color:var(--warn)}
.md .alert-warning .alert-title{color:var(--warn)}
.md blockquote.alert-caution{border-left-color:var(--err)}
.md .alert-caution .alert-title{color:var(--err)}
.md img{max-width:100%;border-radius:6px}
.md hr{border:0;border-top:1px solid var(--border);margin:1.8em 0}
.table-wrap{overflow-x:auto;margin:1em 0;border:1px solid var(--border);border-radius:10px}
.table-wrap table{border-collapse:collapse;width:100%;font-size:13px}
.table-wrap th,.table-wrap td{border-bottom:1px solid var(--border);padding:7px 11px;
  text-align:left;vertical-align:top}
.table-wrap th{background:var(--panel2);font-weight:700;white-space:nowrap}
.table-wrap tr:last-child td{border-bottom:0}
.table-wrap tbody tr:hover{background:var(--hover)}
.md details{background:var(--panel2);border:1px solid var(--border);border-radius:8px;
  padding:.5em .8em;margin:1em 0}
.md summary{cursor:pointer;font-weight:600}
.md kbd{background:var(--panel2);border:1px solid var(--border);border-bottom-width:2px;
  border-radius:5px;padding:1px 5px;font-size:.85em}
.md mark{background:var(--mark);color:inherit;border-radius:3px;padding:0 2px}

/* 문법 하이라이팅 */
.hl-c{color:var(--hl-c);font-style:italic}
.hl-k{color:var(--hl-k)}
.hl-t{color:var(--hl-t)}
.hl-s{color:var(--hl-s)}
.hl-n{color:var(--hl-n)}
.hl-f{color:var(--hl-f)}
.hl-v{color:var(--hl-v)}
.hl-g{color:var(--hl-g)}
.hl-a{color:var(--hl-a)}
.hl-dm,.hl-dh{color:var(--hl-meta)}
.hl-da{color:var(--hl-add);background:var(--hl-add-bg);display:inline-block;width:100%}
.hl-dd{color:var(--hl-del);background:var(--hl-del-bg);display:inline-block;width:100%}

/* 텍스트 뷰 */
.txt{border:1px solid var(--border);border-radius:10px;background:var(--panel);overflow:auto;
  font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.7px;
  line-height:1.62}
.txt .ln{display:flex}
.txt .ln:target,.txt .ln.hit{background:var(--active)}
.txt .n{flex:none;width:52px;text-align:right;padding:0 12px 0 6px;color:var(--dim);
  user-select:none;border-right:1px solid var(--border);position:sticky;left:0;
  background:var(--panel)}
.txt .l{padding:0 14px;white-space:pre}
.txt.wrap .l{white-space:pre-wrap;word-break:break-all}

:root{--ed-lh:20px;--ed-fs:12.7px}
.ed-wrap{flex:1 1 auto;display:flex;min-height:0;min-width:0}
.ed-gutter{flex:none;width:54px;overflow:hidden;text-align:right;
  padding:10px 8px 10px 0;color:var(--dim);background:var(--panel2);
  border-right:1px solid var(--border);font-family:ui-monospace,SFMono-Regular,Menlo,
  Consolas,monospace;font-size:var(--ed-fs);line-height:var(--ed-lh);user-select:none;
  white-space:pre}
.split{display:flex;height:calc(100vh - 178px);border:1px solid var(--border);
  border-radius:10px;overflow:hidden;background:var(--panel)}
.split .pane{display:flex;flex-direction:column;min-width:0;flex:1 1 50%}
.pane-h{display:flex;align-items:center;gap:6px;padding:5px 10px;flex:none;
  border-bottom:1px solid var(--border);background:var(--panel2);font-size:11.5px;
  color:var(--dim)}
.pane-h .btn.sm{margin-left:auto;padding:2px 11px;font-size:11.5px}
.pane-h .btn.sm.on{background:var(--warn)}
.dirty{color:var(--warn);margin-left:8px}
.pane-h .ico.sync{font-size:11px;padding:1px 8px;border-color:var(--border);
  border-style:solid;border-width:1px;margin-left:8px;opacity:.6}
.pane-h .ico.sync.on{opacity:1;background:var(--active)}
#ed{flex:1 1 auto;width:100%;resize:none;border:0;outline:none;background:var(--panel);
  color:var(--fg);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
  font-size:var(--ed-fs);line-height:var(--ed-lh);padding:10px 12px;tab-size:2;
  white-space:pre;overflow:auto}
/* 편집 모드: 여백 줄이고 폭을 최대로 */
body.editing .doc{max-width:none;padding:8px 12px 12px}
body.editing .docbar{padding-bottom:8px}
body.editing .split{height:calc(100vh - 104px)}
body.editing .pv{padding:10px 14px}
.vres{flex:none;width:7px;cursor:col-resize;background:var(--panel2);
  border-left:1px solid var(--border);border-right:1px solid var(--border)}
.vres:hover,.vres.dragging{background:var(--accent);opacity:.55}
.pv{flex:1 1 auto;overflow:auto;padding:14px 18px}
.pv .md{font-size:14px}
.pv .txt{border:0}
.pvframe{flex:1 1 auto;width:100%;border:0;background:#fff}

/* 검색 결과 */
#results{position:fixed;inset:44px 0 0 0;background:var(--bg);z-index:15;overflow:auto;
  padding:18px 22px 60px}
#results .rhead{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.res{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:10px 12px;
  margin-bottom:8px;max-width:1000px}
.res .rp{font-family:ui-monospace,Menlo,monospace;font-size:12px;cursor:pointer}
.res .rp:hover{text-decoration:underline}
.res .rl{font-family:ui-monospace,Menlo,monospace;font-size:11.8px;color:var(--dim);
  padding:2px 0 2px 10px;cursor:pointer;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.res .rl:hover{color:var(--fg);background:var(--hover);border-radius:4px}
.res .rl b{color:var(--fg);background:var(--mark);border-radius:2px}

#toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%) translateY(20px);
  background:var(--panel);border:1px solid var(--border);box-shadow:var(--shadow);
  border-radius:999px;padding:7px 16px;font-size:12.5px;opacity:0;pointer-events:none;
  transition:.18s;z-index:60}
#toast.on{opacity:1;transform:translateX(-50%) translateY(0)}
.spin{display:inline-block;width:12px;height:12px;border:2px solid var(--border);
  border-top-color:var(--accent);border-radius:50%;animation:sp .7s linear infinite}
@keyframes sp{to{transform:rotate(360deg)}}
@media (max-width:900px){
  #side{position:absolute;z-index:18;height:calc(100% - 44px);box-shadow:var(--shadow)}
  #tocbar{display:none} .doc{padding:18px 16px 100px} #crumb{display:none}
}
@media print{
  header,#side,#tocbar,.docbar{display:none!important}
  .doc{max-width:none;padding:0}
}
</style>
</head>
<body>
<header>
  <button class="ico" id="btn-side" title="사이드바 [b]">&#9776;</button>
  <span class="brand">docs viewer</span>
  <div class="tabs">
    <button class="tab on" data-mode="fs">로컬</button>
    <button class="tab" data-mode="drive">Drive</button>
  </div>
  <div id="crumb"></div>
  <div class="grow"></div>
  <input id="q" placeholder="검색  /" spellcheck="false" autocomplete="off">
  <button class="ico" id="btn-scope" title="검색 범위: 전체 폴더">전체</button>
  <button class="ico" id="btn-star" title="북마크 [s]">&#9734;</button>
  <button class="ico" id="btn-reload" title="새로고침 [r]">&#8635;</button>
  <button class="ico" id="btn-toc" title="목차 [t]">&#9776;&#8202;</button>
  <button class="ico" id="btn-width" title="본문 폭 [w]">&#8596;</button>
  <button class="ico" id="btn-theme" title="테마">&#9686;</button>
</header>
<div id="wrap">
  <aside id="side">
    <div class="side-top" id="fs-top">
      <div class="side-row">
        <input id="filter" placeholder="트리에서 이름 필터" autocomplete="off">
        <button class="ico" id="btn-tree-reload"
          title="트리 새로고침 - 파일이 추가·삭제됐을 때"><span>&#8635;</span></button>
      </div>
    </div>
    <div class="tree" id="tree"></div>
    <div class="drive-pane" id="drive-pane" hidden></div>
    <div class="side-foot">
      <details id="bm-box" open><summary>북마크</summary><div id="bm"></div></details>
      <details id="rc-box"><summary>최근 본 문서</summary><div id="rc"></div></details>
    </div>
  </aside>
  <div id="resizer" title="드래그해서 너비 조절 (더블클릭: 기본값)"></div>
  <main id="main"><div class="doc" id="doc"></div></main>
  <aside id="tocbar" class="empty"><div class="toc-h" id="toc-h">목차</div>
    <div id="toc"></div></aside>
</div>
<div id="results" hidden></div>
<div id="toast"></div>
<script>
'use strict';
var CFG = null, ST = {mode:'fs', root:null, path:'', doc:null, drive:{id:'root', stack:[]},
                     mtime:0, expanded:{}, tree:{}, loading:{}, searchScope:'all'};
var $ = function(s){ return document.querySelector(s); };
var LS = {
  get:function(k,d){ try{ var v=localStorage.getItem('dv.'+k); return v==null?d:JSON.parse(v); }
                     catch(e){ return d; } },
  set:function(k,v){ try{ localStorage.setItem('dv.'+k, JSON.stringify(v)); }catch(e){} }
};
function esc(s){ return String(s==null?'':s).replace(/[&<>"']/g, function(c){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]; }); }
function toast(msg, ms){
  var t=$('#toast'); t.textContent=msg; t.classList.add('on');
  clearTimeout(toast._t); toast._t=setTimeout(function(){ t.classList.remove('on'); }, ms||1800);
}
function api(path, params){
  var u = path;
  if (params){ var p=[]; for (var k in params){ if(params[k]!=null&&params[k]!=='')
    p.push(encodeURIComponent(k)+'='+encodeURIComponent(params[k])); }
    if (p.length) u += (u.indexOf('?')<0?'?':'&')+p.join('&'); }
  return fetch(u, {credentials:'same-origin'}).then(function(r){
    return r.json().catch(function(){ throw new Error('HTTP '+r.status); }).then(function(j){
      if (!r.ok || j.error) throw new Error(j.error || ('HTTP '+r.status));
      return j;
    });
  });
}
function icon(kind, isDir){
  if (isDir) return '&#128193;';
  return {md:'&#128209;', html:'&#127760;', text:'&#128196;', csv:'&#128202;', image:'&#128444;',
          pdf:'&#128213;', office:'&#128195;', gstub:'&#9729;', binary:'&#128230;'}[kind]
         || '&#128196;';
}

/* ---------------- 라우팅 ---------------- */
function routeOf(){
  var h = decodeURIComponent(location.hash.replace(/^#/,''));
  if (!h) return null;
  var qi = h.indexOf('?'), extra = {};
  if (qi >= 0){
    h.slice(qi+1).split('&').forEach(function(kv){
      var p = kv.split('='); extra[p[0]] = decodeURIComponent(p[1]||''); });
    h = h.slice(0, qi);
  }
  var m = /^fs\/([^\/]+)\/?(.*)$/.exec(h);
  if (m) return {mode:'fs', root:m[1], path:m[2], extra:extra};
  m = /^drive\/(.+)$/.exec(h);
  if (m) return {mode:'drive', id:m[1], extra:extra};
  return {mode:'anchor', id:h};
}
function go(mode, a, b, extra){
  var h = mode==='fs' ? 'fs/'+a+'/'+String(b||'').split('/').map(encodeURIComponent).join('/')
                      : 'drive/'+a;
  if (extra) h += '?' + extra;
  if (location.hash === '#'+h) onRoute(); else location.hash = h;
}
function onRoute(){
  var r = routeOf();
  if (!r){ showWelcome(); return; }
  if (r.mode === 'anchor'){
    var el = document.getElementById(r.id);
    if (el) el.scrollIntoView({block:'start'});
    return;
  }
  if (r.mode === 'fs'){ setMode('fs'); openFs(r.root, r.path, r.extra); }
  else { setMode('drive'); openDrive(r.id, r.extra); }
}

/* ---------------- 트리 (모든 루트를 그룹으로 동시 표시) ---------------- */
function buildTree(){
  var host = $('#tree');
  host.innerHTML = '';
  ST.firstRun = !Object.keys(ST.expanded).length;   // 첫 방문이면 루트를 다 펼쳐둔다
  (CFG.roots||[]).forEach(function(r){
    var row = document.createElement('div');
    row.className = 'node rootnode';
    row.dataset.root = r.id; row.dataset.path = ''; row.dataset.dir = '1';
    row.title = r.path;
    row.innerHTML = '<span class="tw">&#9656;</span>'
      + '<span class="ic">'+(r.cloud?'&#9729;':'&#128449;')+'</span>'
      + '<span class="nm">'+esc(r.name)+'</span>';
    var kids = document.createElement('div');
    kids.className = 'kids rootkids';
    kids.hidden = true;
    kids.dataset.root = r.id;
    row.onclick = function(ev){ ev.stopPropagation(); toggleDir(row, kids, r.id, '', 0); };
    host.appendChild(row); host.appendChild(kids);
    if (ST.expanded[r.id+':'] || ST.firstRun) row.onclick(new Event('x'));
  });
  markSelected();
}
/* 지연 로딩이 다 끝날 때까지 기다린다. 폴더를 펼치면 그 안에서 또 로딩이 시작되므로
   ST.loading 이 빌 때까지 반복해서 기다려야 한다 (무한루프 방지로 횟수를 막아둔다). */
function treeSettled(tries){
  var pend = Object.keys(ST.loading).map(function(k){ return ST.loading[k]; });
  if (!pend.length || (tries||0) >= 20) return Promise.resolve();
  return Promise.all(pend).then(function(){ return treeSettled((tries||0)+1); });
}
/* 트리만 다시 읽는다. 열려 있는 문서는 건드리지 않는다 (헤더의 [r] 은 문서까지 새로 읽는다).
   외부에서 파일이 추가·삭제됐을 때 쓰라고 만든 것이라, 펼침 상태와 스크롤 위치는 그대로 둔다.
   일부러 접어둔 폴더를 다시 펼치지도 않는다. */
function refreshTree(){
  if (ST.mode !== 'fs') return;
  var host = $('#tree'), top = host.scrollTop, btn = $('#btn-tree-reload');
  btn.classList.add('busy');
  ST.tree = {};                       // 캐시를 비워야 서버에서 다시 읽는다
  buildTree();
  treeSettled().then(function(){
    host.scrollTop = top;
    btn.classList.remove('busy');
    toast('트리 새로고침');
  });
}
function toggleDir(row, kids, rid, rel, depth, forceOpen){
  var key = rid+':'+rel, tw = row.querySelector('.tw');
  if (kids.hidden || forceOpen){
    kids.hidden = false; if (tw) tw.innerHTML = '&#9662;';
    ST.expanded[key] = 1;
    if (!kids.dataset.loaded){
      kids.dataset.loaded = '1';
      kids.innerHTML = '<div class="muted" style="padding:4px 8px"><i class="spin"></i></div>';
      var pr = loadTree(rid, rel, kids, depth+1).then(function(){ delete ST.loading[key]; });
      ST.loading[key] = pr;                 // 진행 중 로딩을 기다릴 수 있게 보관
      return pr;
    }
    return ST.loading[key] || Promise.resolve();
  } else {
    kids.hidden = true; if (tw) tw.innerHTML = '&#9656;';
    delete ST.expanded[key];
  }
  LS.set('expanded', ST.expanded);
  return Promise.resolve();
}
function loadTree(rid, rel, container, depth){
  return api('/api/tree', {root:rid, path:rel}).then(function(d){
    ST.tree[rid+':'+rel] = d.entries;
    renderNodes(d.entries, container, depth);
    LS.set('expanded', ST.expanded);
    applyFilter();
  }).catch(function(e){
    container.innerHTML = '<div class="muted" style="padding:6px">'+esc(e.message)+'</div>';
  });
}
/* 특정 파일이 열리면 조상 폴더를 펼쳐서 보여준다 */
function reveal(rid, rel){
  var host = $('#tree');
  var rootRow = host.querySelector('.rootnode[data-root="'+rid+'"]');
  if (!rootRow) return Promise.resolve();
  var kids = rootRow.nextElementSibling;
  var segs = String(rel||'').split('/').filter(Boolean);
  segs.pop();                                    // 마지막은 파일명
  var chain = toggleDir(rootRow, kids, rid, '', 0, true);
  var acc = '';
  segs.forEach(function(seg){
    acc = acc ? acc+'/'+seg : seg;
    var path = acc;
    chain = chain.then(function(){
      var row = host.querySelector('.node[data-root="'+rid+'"][data-path="'+cssEsc(path)+'"]');
      if (!row) return;
      return toggleDir(row, row.nextElementSibling, rid, path, path.split('/').length, true);
    });
  });
  return chain.then(function(){
    markSelected();
    var sel = host.querySelector('.node.sel');
    if (sel && sel.scrollIntoView) sel.scrollIntoView({block:'nearest'});
  });
}
function cssEsc(s){ return String(s).replace(/["\\]/g, '\\$&'); }
function renderNodes(entries, container, depth){
  container.innerHTML = '';
  entries.forEach(function(en){
    var row = document.createElement('div');
    row.className = 'node';
    row.dataset.root = en.root;
    row.dataset.path = en.path; row.dataset.dir = en.dir ? '1':'';
    row.title = en.name + (en.sizeText ? '  ('+en.sizeText+')' : '')
      + (en.remote ? '  [클라우드 전용]' : '') + '\n' + en.path;
    row.innerHTML = '<span class="tw">'+(en.dir?'&#9656;':'')+'</span>'
      + '<span class="ic">'+icon(en.kind, en.dir)+'</span>'
      + '<span class="nm">'+esc(en.name)+'</span>'
      + '<span class="sz">'+(en.remote?'<span class="cloudmark" title="클라우드 전용 - '
        + '열면 다운로드">&#9729;</span> ':'')+esc(en.sizeText||'')+'</span>';
    var kids = document.createElement('div');
    kids.className = 'kids'; kids.hidden = true;
    row.onclick = function(ev){
      ev.stopPropagation();
      if (en.dir) toggleDir(row, kids, en.root, en.path, depth);
      else go('fs', en.root, en.path);
    };
    container.appendChild(row); container.appendChild(kids);
    if (en.dir && ST.expanded[en.root+':'+en.path])
      toggleDir(row, kids, en.root, en.path, depth, true);
  });
  if (!container.children.length)
    container.innerHTML = '<div class="muted" style="padding:6px">비어 있음</div>';
  markSelected();
}
function markSelected(){
  document.querySelectorAll('#tree .node').forEach(function(n){
    n.classList.toggle('sel', ST.mode==='fs' && n.dataset.root===ST.root
                              && n.dataset.path===ST.path && !n.dataset.dir);
  });
}
/* 로드된 노드에 대한 이름 필터 (전체 검색은 / 사용) */
function applyFilter(){
  var q = ($('#filter').value||'').trim().toLowerCase();
  var host = $('#tree');
  host.classList.toggle('filtering', !!q);
  var rows = host.querySelectorAll('.node');
  if (!q){ rows.forEach(function(n){ n.classList.remove('hide','fmatch'); }); return; }
  rows.forEach(function(n){
    var nm = (n.querySelector('.nm')||{}).textContent || '';
    var hit = nm.toLowerCase().indexOf(q) >= 0;
    n.classList.toggle('fmatch', hit);
    n.classList.toggle('hide', !hit && !n.classList.contains('rootnode'));
  });
  // 매칭된 노드의 조상은 보이게
  host.querySelectorAll('.node.fmatch').forEach(function(n){
    var box = n.parentNode;
    while (box && box !== host){
      var owner = box.previousElementSibling;
      if (owner && owner.classList && owner.classList.contains('node'))
        owner.classList.remove('hide');
      box = box.parentNode;
    }
  });
}

/* ---------------- 문서 열기 ---------------- */
function openFs(rid, rel, extra){
  ST.mode='fs';
  ST.root = rid;
  ST.path = rel;
  reveal(rid, rel);
  $('#doc').innerHTML = '<div class="muted"><i class="spin"></i> 불러오는 중…</div>';
  return api('/api/doc', {root:rid, path:rel}).then(function(d){
    ST.doc = d; ST.mtime = d.mtime||0;
    render(d, extra||{});
    if (d.kind !== 'dir') pushRecent({mode:'fs', root:rid, path:rel, name:d.name, kind:d.kind});
    markSelected(); syncStar();
  }).catch(function(e){ showError(e); });
}
function openDrive(id, extra){
  ST.mode='drive'; ST.path = id;
  $('#doc').innerHTML = '<div class="muted"><i class="spin"></i> Drive 문서 가져오는 중…</div>';
  return api('/api/gdrive/doc', {id:id}).then(function(d){
    ST.doc = d; ST.mtime = 0;
    render(d, extra||{});
    pushRecent({mode:'drive', id:id, name:d.name, kind:d.kind});
    syncStar();
  }).catch(function(e){ showError(e); });
}
function showError(e){
  $('#doc').innerHTML = '<div class="card err">'+esc(e.message||String(e))+'</div>';
  setToc([]);
}
function crumb(d){
  var c = $('#crumb');
  if (!d){ c.innerHTML=''; return; }
  if (ST.mode==='drive'){ c.innerHTML = 'Drive / <b>'+esc(d.name||'')+'</b>'; return; }
  var parts = (d.path||'').split('/').filter(Boolean), out = [esc(d.rootName||'')];
  parts.forEach(function(p,i){ out.push(i===parts.length-1 ? '<b>'+esc(p)+'</b>' : esc(p)); });
  c.innerHTML = out.join(' / ');
  document.title = (parts.length ? parts[parts.length-1] : d.rootName) + ' - docs viewer';
}
// ---- mermaid ----------------------------------------------------------------
// 서버는 ```mermaid 펜스를 <pre class="mermaid" data-mermaid> 로만 내려보낸다.
// 여기서 스크립트를 한 번 받아 그림으로 바꾼다. data-mermaid 가 남아 있으면
// "아직 안 그린 것", data-src 는 다시 그릴 때 쓸 원문이다.
var MM = {p:null, n:0};
function mmOn(){ return !!(CFG && CFG.mermaid && CFG.mermaid.on); }
function mmTheme(){
  var t = document.documentElement.getAttribute('data-theme');
  if (!t) t = matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  return t === 'dark' ? 'dark' : 'default';
}
function mmInit(m){
  m.initialize({startOnLoad:false, securityLevel:'strict', theme:mmTheme(),
                fontFamily:'inherit'});
}
function mmLoad(){
  if (MM.p) return MM.p;
  MM.p = new Promise(function(res, rej){
    var s = document.createElement('script');
    s.src = '/assets/mermaid-' + CFG.mermaid.ver + '.min.js';
    s.onload = function(){
      if (!window.mermaid){ MM.p = null; rej(new Error('mermaid 로드 실패')); return; }
      mmInit(window.mermaid); res(window.mermaid);
    };
    s.onerror = function(){                  // 다음 문서에서 다시 시도할 수 있게 비운다
      MM.p = null;
      rej(new Error('mermaid 스크립트를 불러오지 못했습니다 (오프라인?)'));
    };
    document.head.appendChild(s);
  });
  return MM.p;
}
function mmFail(el, e){
  el.classList.add('mm-err');
  el.innerHTML = '<div class="mm-msg">'+esc(String((e&&e.message)||e))+'</div>'
    + '<code>'+esc(el.getAttribute('data-src')||'')+'</code>';
}
function drawMermaid(scope){
  var list = [].slice.call((scope||document).querySelectorAll('pre.mermaid[data-mermaid]'));
  if (!list.length || !mmOn()) return;
  list.forEach(function(el){
    if (el.getAttribute('data-src') === null) el.setAttribute('data-src', el.textContent);
    el.removeAttribute('data-mermaid');
  });
  mmLoad().then(function(m){
    list.forEach(function(el){
      var id = 'mmd' + (++MM.n);
      m.render(id, el.getAttribute('data-src') || '').then(function(r){
        el.classList.remove('mm-err');
        el.innerHTML = r.svg;
        if (r.bindFunctions) r.bindFunctions(el);
      }).catch(function(e){
        var junk = document.getElementById('d'+id);   // 실패 시 남는 임시 노드
        if (junk) junk.remove();
        mmFail(el, e);
      });
    });
  }).catch(function(e){ list.forEach(function(el){ mmFail(el, e); }); });
}
function redrawMermaid(){                    // 테마가 바뀌면 색을 다시 입힌다
  if (!MM.p) return;
  var list = document.querySelectorAll('pre.mermaid[data-src]');
  if (!list.length) return;
  mmLoad().then(function(m){
    mmInit(m);
    [].forEach.call(list, function(el){
      el.classList.remove('mm-err');
      el.setAttribute('data-mermaid', '1');
    });
    drawMermaid();
  }).catch(function(){});
}
function render(d, extra){
  crumb(d);
  document.body.classList.remove('editing');   // 편집 모드는 split 진입 시에만
  var bar = [];
  if (d.sizeText) bar.push('<span class="chip">'+esc(d.sizeText)+'</span>');
  if (d.mtime) bar.push('<span class="chip">'+new Date(d.mtime*1000).toLocaleString('ko-KR')
    +'</span>');
  if (d.truncated) bar.push('<span class="chip" style="color:var(--warn)">일부만 표시</span>');
  if (d.viaStub) bar.push('<span class="chip">Drive 에서 변환</span>');
  if (d.remote) bar.push('<span class="chip" title="아직 로컬에 없던 파일 - 열면서 다운로드'
    + '됩니다">&#9729; 클라우드</span>');
  if (d.webViewLink) bar.push('<a class="chip" href="'+esc(d.webViewLink)
    +'" target="_blank" rel="noreferrer">Drive에서 열기</a>');
  var body = '', toc = d.toc || [];

  if (d.kind === 'dir'){
    body = '<h2 style="margin:0 0 14px">'+esc(d.name)+'</h2>' + dirList(d);
  } else if (d.kind === 'md'){
    body = '<article class="md">'+ d.html +'</article>';
    if (d.source) bar.push(btn('split', '분할 편집'));
  } else if (d.kind === 'csv'){
    body = '<article class="md">'+ d.html +'</article>';
    bar.push(btn('split', '분할 편집'));
  } else if (d.kind === 'html'){
    var url = d.rawUrl || d.url;
    body = '<iframe class="frame" id="ifr" src="'+esc(url)+'" '
      + 'sandbox="allow-same-origin allow-popups allow-popups-to-escape-sandbox"></iframe>';
    bar.push(btn('split', '분할 편집'));
    bar.push(btn('scripts', '스크립트 허용'));
  } else if (d.kind === 'text'){
    body = textView(d.content||'', d.lines);
    bar.push(btn('split', '편집'));
    bar.push(btn('wrap', '줄바꿈'));
    if (d.hlLang) bar.push('<span class="chip">'+esc(d.hlLang)+'</span>');
  } else if (d.kind === 'image'){
    body = '<div class="card" style="text-align:center"><img src="'+esc(d.rawUrl)
      +'" style="max-width:100%;border-radius:8px"></div>';
  } else if (d.kind === 'pdf'){
    body = '<iframe class="frame" src="'+esc(d.rawUrl)+'"></iframe>';
  } else if (d.kind === 'gstub'){
    body = '<div class="card"><h3 style="margin:0 0 6px">'+esc(d.name)+'</h3>'
      + '<p class="muted">'+esc(d.docType||'Google Drive 문서')
      + (d.email ? ' · '+esc(d.email) : '')+'</p>'
      + (d.driveError ? '<p class="muted">'+esc(d.driveError)+'</p>' : '')
      + '<p style="display:flex;gap:8px;flex-wrap:wrap">'
      + (d.webViewLink ? '<a class="btn" href="'+esc(d.webViewLink)
         + '" target="_blank" rel="noreferrer">Drive에서 열기</a>' : '')
      + (d.driveId && !(CFG.drive&&CFG.drive.authed)
         ? '<a class="btn sec" href="/oauth/start" target="_blank">Drive 연결</a>' : '')
      + (d.driveId ? '<a class="btn sec" href="#drive/'+esc(d.driveId)
         + '">뷰어에서 열기</a>' : '')
      + '</p><p class="muted" style="font-size:12px">이 파일은 실제 문서가 아니라 '
      + 'Google Drive 바로가기(doc_id 만 담긴 JSON)입니다. Drive 를 연결하면 '
      + '내용을 여기서 바로 렌더합니다.</p></div>';
  } else if (d.kind === 'office'){
    body = d.officeUrl
      ? '<iframe class="frame" src="'+esc(d.officeUrl)+'"></iframe>'
      : '<div class="card"><p class="err">'+esc(d.officeError||'변환 불가')+'</p>'
        + '<p class="muted">LibreOffice 를 설치하면 docx/xlsx/pptx 를 미리보기할 수 있습니다.<br>'
        + 'macOS: <code>brew install --cask libreoffice</code></p>'
        + '<p><a class="btn" href="'+esc(d.rawUrl)+'?dl=1">다운로드</a></p></div>';
  } else {
    body = '<div class="card"><p>미리보기를 지원하지 않는 형식입니다.</p>'
      + '<p><a class="btn" href="'+esc(d.rawUrl||'#')+(d.rawUrl&&d.rawUrl.indexOf('?')<0?'?dl=1':'&dl=1')
      + '">다운로드</a></p></div>';
  }
  if (d.rawUrl && d.kind !== 'binary')
    bar.push('<a class="chip" href="'+esc(d.rawUrl)
      + (d.rawUrl.indexOf('?')<0?'?dl=1':'&dl=1')+'">다운로드</a>');
  // 목차를 꺼둔 사람은 시트 목록이 통째로 안 보인다. 있다는 사실만 알려준다.
  if (d.sheets && d.sheets.length > 1 && document.body.classList.contains('no-toc'))
    bar.push('<span class="chip">시트 '+d.sheets.length+'개 · <b>t</b> 로 목차 열기</span>');
  // 본문 960px 제한은 '글' 을 읽기 좋은 폭이다. 슬라이드(16:9 pdf)·office·넓은 표에
  // 적용하면 내용은 쪼그라들고 좌우 여백만 커진다. 이런 종류는 폭 제한을 푼다.
  $('#doc').classList.toggle('wide', ['pdf','office','csv'].indexOf(d.kind) >= 0);
  $('#doc').innerHTML = '<div class="docbar">'+bar.join('')+'</div>' + body;
  setToc(toc, d);
  bindDocBar(d);
  drawMermaid($('#doc'));
  var main = $('#main'); main.scrollTop = 0;
  if (extra.h){
    var el = document.getElementById(extra.h);
    if (el) setTimeout(function(){ el.scrollIntoView({block:'start'}); }, 30);
  }
  if (extra.edit === '1' && SPLIT_KINDS[d.kind]){        // #...?edit=1 로 바로 편집 진입
    var sb = $('#doc').querySelector('[data-act="split"]');
    if (sb) sb.click();
  }
  if (extra.l){
    var ln = document.getElementById('L'+extra.l);
    if (ln){ ln.classList.add('hit');
      setTimeout(function(){ ln.scrollIntoView({block:'center'}); }, 30); }
  }
}
function btn(act, label){
  return '<button class="ico" data-act="'+act+'" style="border-color:var(--border);'
    + 'font-size:12px;padding:2px 10px">'+esc(label)+'</button>';
}
function dirList(d){
  if (!d.entries.length) return '<p class="muted">빈 폴더</p>';
  var rows = d.entries.map(function(en){
    return '<div class="node" data-p="'+esc(en.path)+'" data-d="'+(en.dir?1:'')+'">'
      + '<span class="ic">'+icon(en.kind, en.dir)+'</span>'
      + '<span class="nm">'+esc(en.name)+'</span>'
      + '<span class="sz">'+(en.remote?'&#9729; ':'')+esc(en.sizeText||'')
      + '</span></div>';
  }).join('');
  return '<div class="card" style="padding:8px">'+rows+'</div>';
}
function textView(content, hlLines){
  var cap = 20000, out = [];
  var lines = hlLines || String(content||'').split('\n');
  var n = Math.min(lines.length, cap);
  for (var i=0;i<n;i++){
    var body = hlLines ? (lines[i] || ' ') : esc(lines[i]||' ');
    out.push('<div class="ln" id="L'+(i+1)+'"><span class="n">'+(i+1)
      + '</span><span class="l">'+body+'</span></div>');
  }
  var more = lines.length>cap ? '<div class="muted" style="padding:8px">'
    + (lines.length-cap)+' 줄 생략</div>' : '';
  return '<div class="txt'+(LS.get('wrap',false)?' wrap':'')+'" id="txt">'
    + out.join('') + more + '</div>';
}
function bindDocBar(d){
  $('#doc').querySelectorAll('[data-act]').forEach(function(b){
    b.onclick = function(){
      var act = b.dataset.act;
      if (act === 'split'){
        if (b.classList.contains('on')){        // 나가기
          if (ST.dirty && !confirm('저장하지 않은 변경이 있습니다. 나가시겠습니까?')) return;
          setDirty(false); document.body.classList.remove('editing');
          render(d, {}); return;
        }
        b.classList.add('on');
        document.body.classList.add('editing');
        var host = $('#doc'), barEl = host.querySelector('.docbar');
        while (host.lastChild && host.lastChild !== barEl) host.removeChild(host.lastChild);
        host.insertAdjacentHTML('beforeend', splitMarkup(d));
        bindSplit(d);
      } else if (act === 'scripts'){
        var f = $('#ifr'); if (!f) return;
        b.classList.toggle('on');
        var on = b.classList.contains('on');
        f.setAttribute('sandbox', on ? 'allow-scripts allow-popups'
                                     : 'allow-same-origin allow-popups');
        var u = (d.rawUrl||'').split('?')[0];
        f.src = u + (on ? '?scripts=1' : '');
        toast(on ? '스크립트 허용 (격리 실행)' : '스크립트 차단');
      } else if (act === 'wrap'){
        b.classList.toggle('on');
        var t = $('#txt'); if (t) t.classList.toggle('wrap');
        LS.set('wrap', b.classList.contains('on'));
      }
    };
    if (b.dataset.act === 'wrap' && LS.get('wrap',false)) b.classList.add('on');
  });
  $('#doc').querySelectorAll('.card .node').forEach(function(n){
    n.onclick = function(){ go('fs', ST.root, n.dataset.p); };
  });
}

/* ---------------- 분할 편집 (소스 + 미리보기) ---------------- */
var SPLIT_KINDS = {md:1, html:1, csv:1, text:1};
function splitMarkup(d){
  var canEdit = !!(CFG && CFG.canEdit);
  var pv = d.kind === 'html'
    ? '<iframe class="pvframe" id="pvframe"></iframe>'
    : '<div class="pv" id="pv"></div>';
  return '<div class="split" id="split">'
    + '<div class="pane pane-ed">'
    +   '<div class="pane-h"><span>소스</span>'
    +     '<span class="muted" id="ed-pos" style="font-variant-numeric:tabular-nums"></span>'
    +     '<span class="dirty" id="dirty" hidden>'
    +     '&#9679; 수정됨</span>'
    +     (canEdit
             ? '<button class="btn sm" id="btn-save">저장</button>'
               + '<span class="muted" style="margin-left:6px">&#8984;S</span>'
             : '<span class="muted" style="margin-left:auto">읽기 전용 '
               + '(--allow-edit 로 저장 활성화)</span>')
    +   '</div>'
    +   '<div class="ed-wrap">'
    +     '<div class="ed-gutter" id="edgut"></div>'
    +     '<textarea id="ed" spellcheck="false" wrap="off"></textarea>'
    +   '</div>'
    + '</div>'
    + '<div class="vres" id="vres" title="드래그로 비율 조절 (더블클릭: 50%)"></div>'
    + '<div class="pane pane-pv">'
    +   '<div class="pane-h"><span>미리보기</span>'
    +     '<span class="muted" id="pv-state" style="margin-left:auto"></span>'
    +     '<button class="ico sync" id="btn-sync" title="소스와 스크롤 동기화">'
    +       '&#8645; 동기화</button></div>'
    +   pv
    + '</div>'
    + '</div>';
}
function dirOf(p){ var i = String(p||'').lastIndexOf('/'); return i<0 ? '' : p.slice(0,i); }
function bindSplit(d){
  var ed = $('#ed'), split = $('#split');
  if (!ed) return;
  ed.value = (d.source != null ? d.source : (d.content || ''));
  try { ed.setSelectionRange(0, 0); } catch (e) {}   // 캐럿을 맨 위로
  ed.scrollTop = 0;
  ST.dirty = false;
  var gut = $('#edgut'), lastLines = -1;
  function renderGutter(){
    var n = ed.value.split('\n').length;
    if (n === lastLines) return;
    lastLines = n;
    var out = [];
    for (var i = 1; i <= n; i++) out.push(i);
    gut.textContent = out.join('\n');
  }
  function syncGutter(){ gut.scrollTop = ed.scrollTop; }
  renderGutter();
  ed.addEventListener('scroll', syncGutter);
  var ratio = LS.get('splitRatio', 50);
  applyRatio(ratio);
  updatePreview(d, ed.value);

  var t = null;
  ed.addEventListener('input', function(){
    setDirty(true);
    renderGutter(); syncGutter();
    clearTimeout(t);
    t = setTimeout(function(){ updatePreview(d, ed.value); }, 350);
  });
  ed.addEventListener('keyup', syncGutter);
  ed.addEventListener('click', syncGutter);
  ed.addEventListener('keydown', function(e){
    if (e.key === 'Tab'){                       // 탭은 들여쓰기로
      e.preventDefault();
      var s = ed.selectionStart, en = ed.selectionEnd;
      ed.value = ed.value.slice(0,s) + '  ' + ed.value.slice(en);
      ed.selectionStart = ed.selectionEnd = s + 2;
      setDirty(true); renderGutter();
    } else if ((e.metaKey||e.ctrlKey) && e.key.toLowerCase() === 's'){
      e.preventDefault(); doSave(d);
    }
  });
  var sv = $('#btn-save');
  if (sv) sv.onclick = function(){ doSave(d); };

  /* 좌우 비율 드래그 */
  var vres = $('#vres'), dragging = false;
  function applyRatio(r){
    var l = split.querySelector('.pane-ed'), rp = split.querySelector('.pane-pv');
    l.style.flex = '0 0 ' + r + '%'; rp.style.flex = '1 1 auto';
  }
  window.__applyRatio = applyRatio;
  vres.addEventListener('pointerdown', function(e){
    dragging = true; vres.classList.add('dragging');
    document.body.classList.add('resizing');
    if (vres.setPointerCapture && e.pointerId != null) vres.setPointerCapture(e.pointerId);
  });
  window.addEventListener('pointermove', function(e){
    if (!dragging) return;
    var box = split.getBoundingClientRect();
    var r = Math.min(85, Math.max(15, (e.clientX - box.left) / box.width * 100));
    applyRatio(r);
  });
  window.addEventListener('pointerup', function(){
    if (!dragging) return;
    dragging = false; vres.classList.remove('dragging');
    document.body.classList.remove('resizing');
    var l = split.querySelector('.pane-ed').getBoundingClientRect();
    var box = split.getBoundingClientRect();
    LS.set('splitRatio', Math.round(l.width / box.width * 100));
  });
  vres.addEventListener('dblclick', function(){ applyRatio(50); LS.set('splitRatio', 50); });
  setupSync(d);
  ed.focus({preventScroll: true});
  ed.scrollTop = 0; syncGutter();
  /* 커서 위치 표시 */
  function pos(){
    var upto = ed.value.slice(0, ed.selectionStart).split('\n');
    var el = $('#ed-pos');
    if (el) el.textContent = upto.length + ':' + (upto[upto.length-1].length + 1);
  }
  ed.addEventListener('keyup', pos);
  ed.addEventListener('click', pos);
  ed.addEventListener('input', pos);
  pos();
}
function setDirty(on){
  ST.dirty = !!on;
  var el = $('#dirty'); if (el) el.hidden = !on;
  var sv = $('#btn-save'); if (sv) sv.classList.toggle('on', !!on);
}
/* ---- 소스 <-> 미리보기 스크롤 동기화 ---- */
function lineHeightOf(el){
  var lh = parseFloat(getComputedStyle(el).lineHeight);
  return (lh && !isNaN(lh)) ? lh : 20;
}
function pvScroller(){                        // 스크롤 컨테이너 (html 은 iframe 문서)
  var fr = $('#pvframe');
  if (fr){
    try {
      var doc = fr.contentDocument;
      return doc ? (doc.scrollingElement || doc.documentElement) : null;
    } catch (e){ return null; }
  }
  return $('#pv');
}
function setupSync(d){
  var ed = $('#ed'), btn = $('#btn-sync');
  if (!ed) return;
  ST.sync = LS.get('sync', true);
  if (btn){
    btn.classList.toggle('on', ST.sync);
    btn.onclick = function(){
      ST.sync = !ST.sync; LS.set('sync', ST.sync);
      btn.classList.toggle('on', ST.sync);
      toast(ST.sync ? '스크롤 동기화 켬' : '스크롤 동기화 끔');
      if (ST.sync) edToPv();
    };
  }
  /* 방향별 락: 내가 프로그램적으로 움직인 쪽의 scroll 이벤트만 무시한다.
     (단일 락이면 스크롤을 시작한 쪽도 막혀서 끊겨 보인다) */
  var lockEd = 0, lockPv = 0;

  function edToPv(){
    if (!ST.sync || Date.now() < lockEd) return;
    var box = pvScroller(); if (!box) return;
    lockPv = Date.now() + 120;
    var lh = lineHeightOf(ed);
    var line = Math.floor(ed.scrollTop / lh) + 1;
    var marks = (box.querySelectorAll ? box.querySelectorAll('[data-line]') : []);
    if (!marks.length){                       // 매핑 정보가 없으면 비율 동기화 (html 등)
      var r = ed.scrollTop / Math.max(1, ed.scrollHeight - ed.clientHeight);
      box.scrollTop = r * Math.max(0, box.scrollHeight - box.clientHeight);
      return;
    }
    var prev = null, next = null;
    for (var i = 0; i < marks.length; i++){
      var l = +marks[i].getAttribute('data-line');
      if (l <= line) prev = marks[i]; else { next = marks[i]; break; }
    }
    if (!prev){ box.scrollTop = 0; return; }
    var top = offsetIn(prev, box);
    var pl = +prev.getAttribute('data-line');
    if (next){
      var nl = +next.getAttribute('data-line');
      var frac = nl > pl ? (line - pl) / (nl - pl) : 0;
      top += (offsetIn(next, box) - top) * Math.max(0, Math.min(1, frac));
    }
    box.scrollTop = top;
  }
  function pvToEd(){
    if (!ST.sync || Date.now() < lockPv) return;
    var box = pvScroller(); if (!box) return;
    lockEd = Date.now() + 120;
    var lh = lineHeightOf(ed);
    var marks = (box.querySelectorAll ? box.querySelectorAll('[data-line]') : []);
    if (!marks.length){
      var r = box.scrollTop / Math.max(1, box.scrollHeight - box.clientHeight);
      ed.scrollTop = r * Math.max(0, ed.scrollHeight - ed.clientHeight);
      syncGutterNow();
      return;
    }
    var cur = null, top = box.scrollTop;
    for (var i = 0; i < marks.length; i++){
      if (offsetIn(marks[i], box) <= top + 4) cur = marks[i]; else break;
    }
    var line = cur ? +cur.getAttribute('data-line') : 1;
    ed.scrollTop = Math.max(0, (line - 1) * lh);
    syncGutterNow();
  }
  function offsetIn(el, box){
    if (box === $('#pv'))
      return el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop;
    return el.getBoundingClientRect().top + box.scrollTop;   // iframe 문서 기준
  }
  function syncGutterNow(){
    var g = $('#edgut'); if (g) g.scrollTop = ed.scrollTop;
  }
  ST.edToPv = edToPv;
  ed.addEventListener('scroll', edToPv);
  var pv = $('#pv');
  if (pv) pv.addEventListener('scroll', pvToEd);
  var fr = $('#pvframe');
  if (fr) fr.addEventListener('load', function(){
    try {
      fr.contentWindow.addEventListener('scroll', pvToEd, {passive:true});
    } catch (e){}
    edToPv();
  });
}

function updatePreview(d, text){
  var st = $('#pv-state');
  if (d.kind === 'html'){                      // 서버에 잠깐 올려두고 iframe 으로 렌더
    var f = $('#pvframe'); if (!f) return;
    if (st) st.innerHTML = '<i class="spin"></i>';
    fetch('/api/preview', {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-Docs-Viewer': '1'},
      body: JSON.stringify({kind: 'html', text: text, root: d.root, path: d.path})
    }).then(function(r){ return r.json(); }).then(function(j){
      if (j.error) throw new Error(j.error);
      f.src = j.url;
      if (st) st.textContent = '스크립트 차단 · 상대경로 유지';
    }).catch(function(e){
      if (st) st.innerHTML = '<span class="err">'+esc(e.message)+'</span>';
    });
    return;
  }
  var pv = $('#pv'); if (!pv) return;
  if (st) st.innerHTML = '<i class="spin"></i>';
  fetch('/api/preview', {
    method: 'POST', credentials: 'same-origin',
    headers: {'Content-Type': 'application/json', 'X-Docs-Viewer': '1'},
    body: JSON.stringify({kind: d.kind, text: text, root: d.root, path: d.path})
  }).then(function(r){ return r.json(); }).then(function(j){
    if (j.error) throw new Error(j.error);
    pv.innerHTML = '<article class="md">' + j.html + '</article>';
    drawMermaid(pv);
    if (st) st.textContent = '';
    if (j.toc) setToc(j.toc);
    if (ST.edToPv) ST.edToPv();               // 렌더 후 현재 위치로 다시 맞춘다
  }).catch(function(e){
    if (st) st.innerHTML = '<span class="err">'+esc(e.message)+'</span>';
  });
}
function doSave(d){
  if (!CFG.canEdit){ toast('읽기 전용 모드입니다 (--allow-edit 필요)'); return; }
  var ed = $('#ed'); if (!ed) return;
  var body = {root: d.root, path: d.path, content: ed.value};
  var sv = $('#btn-save'); if (sv) sv.textContent = '저장 중…';
  fetch('/api/save', {
    method: 'POST', credentials: 'same-origin',
    headers: {'Content-Type': 'application/json', 'X-Docs-Viewer': '1'},
    body: JSON.stringify(body)
  }).then(function(r){ return r.json(); }).then(function(j){
    if (j.error) throw new Error(j.error);
    if (sv) sv.textContent = '저장';
    setDirty(false);
    d.source = ed.value; d.content = ed.value;
    ST.mtime = j.mtime;                        // 자동 갱신이 되돌리지 않도록
    if (ST.doc) { ST.doc.source = ed.value; ST.doc.content = ed.value; ST.doc.mtime = j.mtime; }
    toast('저장됨 · ' + j.sizeText);
    updatePreview(d, ed.value);
  }).catch(function(e){
    if (sv) sv.textContent = '저장';
    toast('저장 실패: ' + e.message, 3200);
  });
}

/* ---------------- 목차 ---------------- */
/* office 문서의 iframe 과 시트 앵커 ------------------------------
   LibreOffice 는 시트를 한 파일에 이어 붙이고 각 시트 앞에 <a name="tableN"> 을 남긴다.
   iframe 이 같은 오리진이라 부모가 hash 를 바꿔 점프시키고 스크롤도 추적할 수 있다. */
function sheetFrame(){ return $('#doc').querySelector('iframe.frame'); }
function jumpSheet(id){
  var f = sheetFrame(); if (!f) return;
  // hash 만 바꾸면 다시 로드하지 않고 점프한다 (src 를 갈아끼우면 수 MB 를 다시 그린다)
  try { f.contentWindow.location.hash = id; }
  catch(e){ f.src = f.src.split('#')[0] + '#' + id; }
}
function markSheet(id){
  $('#toc').querySelectorAll('[data-sheet]').forEach(function(x){
    x.classList.toggle('on', x.dataset.sheet === id); });
}
var sheetTick = null;
function watchSheetScroll(){
  var f = sheetFrame(); if (!f) return;
  function attach(){
    var w, doc;
    try { w = f.contentWindow; doc = w.document; }
    catch(e){ return; }                 // 접근이 막히면 클릭 이동만 되고 추적은 포기한다
    w.addEventListener('scroll', function(){
      if (sheetTick) return;
      sheetTick = setTimeout(function(){
        sheetTick = null;
        var cur = null;
        $('#toc').querySelectorAll('[data-sheet]').forEach(function(a){
          var el = doc.getElementsByName(a.dataset.sheet)[0];
          if (el && el.getBoundingClientRect().top <= 80) cur = a.dataset.sheet;
        });
        if (cur) markSheet(cur);
      }, 120);
    });
  }
  f.addEventListener('load', attach);
  try { if (f.contentDocument && f.contentDocument.readyState === 'complete') attach(); }
  catch(e){}
}

function setToc(toc, d){
  var box = $('#toc'), bar = $('#tocbar'), head = $('#toc-h');
  // 시트가 여러 장인 office 문서는 헤딩 대신 시트 목록을 싣는다.
  // '문서 안에서 위치 이동' 이라는 점이 목차와 같고, 32장을 가로 탭으로 훑는 것보다
  // 세로 목록이 훨씬 빨리 찾힌다.
  if (d && d.sheets && d.sheets.length > 1){
    head.textContent = '시트 ' + d.sheets.length;
    bar.classList.remove('empty');
    box.innerHTML = d.sheets.map(function(sh, i){
      return '<a href="#" data-sheet="'+esc(sh.id)+'" title="'+esc(sh.name)+'"'
        + (i===0?' class="on"':'')+'>'+esc(sh.name)+'</a>'; }).join('');
    box.querySelectorAll('[data-sheet]').forEach(function(a){
      a.onclick = function(ev){
        ev.preventDefault(); jumpSheet(a.dataset.sheet); markSheet(a.dataset.sheet); };
    });
    watchSheetScroll();
    return;
  }
  head.textContent = '목차';
  var items = (toc||[]).filter(function(t){ return t.level<=4; });
  if (!items.length){ bar.classList.add('empty'); box.innerHTML=''; return; }
  bar.classList.remove('empty');
  box.innerHTML = items.map(function(t){
    return '<a href="#'+encodeURIComponent(t.id)+'" data-l="'+t.level+'" data-id="'+esc(t.id)
      + '">'+esc(t.text)+'</a>'; }).join('');
  box.querySelectorAll('a').forEach(function(a){
    a.onclick = function(ev){ ev.preventDefault();
      var el = document.getElementById(a.dataset.id);
      if (el) el.scrollIntoView({block:'start'});
      box.querySelectorAll('a').forEach(function(x){ x.classList.remove('on'); });
      a.classList.add('on');
    };
  });
}
var tocTick = null;
$('#main').addEventListener('scroll', function(){
  if (tocTick) return;
  tocTick = setTimeout(function(){
    tocTick = null;
    var hs = $('#doc').querySelectorAll('h1[id],h2[id],h3[id],h4[id]');
    var cur = null, top = $('#main').getBoundingClientRect().top + 80;
    hs.forEach(function(h){ if (h.getBoundingClientRect().top <= top) cur = h.id; });
    if (cur) $('#toc').querySelectorAll('a').forEach(function(a){
      a.classList.toggle('on', a.dataset.id === cur); });
  }, 120);
});

/* ---------------- 검색 ---------------- */
var searchTimer = null;
$('#q').addEventListener('input', function(){
  clearTimeout(searchTimer);
  var v = $('#q').value.trim();
  if (!v){ $('#results').hidden = true; return; }
  searchTimer = setTimeout(function(){ doSearch(v); }, 260);
});
$('#q').addEventListener('keydown', function(e){
  if (e.key === 'Escape'){ $('#q').value=''; $('#results').hidden=true; $('#q').blur(); }
  if (e.key === 'Enter'){ clearTimeout(searchTimer); doSearch($('#q').value.trim()); }
});
function hl(text, needle){
  var i = text.toLowerCase().indexOf(needle.toLowerCase());
  if (i < 0) return esc(text);
  return esc(text.slice(0,i)) + '<b>' + esc(text.slice(i, i+needle.length)) + '</b>'
    + esc(text.slice(i+needle.length));
}
function doSearch(v){
  if (!v) return;
  var box = $('#results'); box.hidden = false;
  box.innerHTML = '<div class="rhead"><i class="spin"></i><span class="muted">검색 중… '
    + esc(v) + '</span></div>';
  var isDrive = ST.mode === 'drive';
  var req = isDrive ? api('/api/gdrive/search', {q:v})
                    : api('/api/search', {q:v, root: (ST.searchScope==='root' ? ST.root : '')});
  req.then(function(d){
    if (isDrive){
      var files = d.files || [];
      box.innerHTML = '<div class="rhead"><b>Drive 검색</b><span class="muted">'
        + files.length + '건</span>' + closeBtn() + '</div>'
        + files.map(function(f){
            return '<div class="res"><div class="rp" data-id="'+esc(f.id)+'">'
              + esc(f.name) + ' <span class="muted">'
              + esc((f.mimeType||'').replace('application/vnd.google-apps.',''))
              + '</span></div></div>'; }).join('');
      box.querySelectorAll('.rp').forEach(function(el){
        el.onclick = function(){ box.hidden=true; go('drive', el.dataset.id); }; });
    } else {
      var rs = d.results || [];
      box.innerHTML = '<div class="rhead"><b>검색 결과</b><span class="muted">' + rs.length
        + '건' + (d.capped ? ' (일부 생략)' : '')
        + (d.skipped ? ' · 클라우드 미다운로드 ' + d.skipped + '개는 파일명만 검색' : '')
        + '</span>' + closeBtn() + '</div>'
        + (rs.length ? rs.map(function(r){
            return '<div class="res"><div class="rp" data-r="'+esc(r.root)+'" data-p="'
              + esc(r.path)+'">' + icon(r.kind,false) + ' ' + hl(r.path, v)
              + ' <span class="muted">'+esc(r.rootName)+'</span></div>'
              + r.lines.map(function(l){
                  return '<div class="rl" data-r="'+esc(r.root)+'" data-p="'+esc(r.path)
                    + '" data-l="'+l.no+'">'+l.no+': '+hl(l.text, v)+'</div>'; }).join('')
              + '</div>'; }).join('')
           : '<p class="muted">결과 없음</p>');
      box.querySelectorAll('.rp').forEach(function(el){
        el.onclick = function(){ box.hidden=true; go('fs', el.dataset.r, el.dataset.p); }; });
      box.querySelectorAll('.rl').forEach(function(el){
        el.onclick = function(){ box.hidden=true;
          go('fs', el.dataset.r, el.dataset.p, 'l='+el.dataset.l); }; });
    }
    var cb = box.querySelector('#res-close');
    if (cb) cb.onclick = function(){ box.hidden = true; };
  }).catch(function(e){
    box.innerHTML = '<div class="card err">'+esc(e.message)+'</div>';
  });
}
function closeBtn(){
  return '<div class="grow"></div><button class="ico" id="res-close" '
    + 'style="border-color:var(--border)">닫기 [esc]</button>';
}

/* ---------------- 북마크 / 최근 ---------------- */
function keyOf(it){ return it.mode==='drive' ? 'drive:'+it.id : 'fs:'+it.root+':'+it.path; }
function curItem(){
  if (!ST.doc) return null;
  return ST.mode==='drive' ? {mode:'drive', id:ST.path, name:ST.doc.name, kind:ST.doc.kind}
    : {mode:'fs', root:ST.root, path:ST.path, name:ST.doc.name, kind:ST.doc.kind};
}
function pushRecent(it){
  var rc = LS.get('recent', []).filter(function(x){ return keyOf(x)!==keyOf(it); });
  rc.unshift(it); LS.set('recent', rc.slice(0,25)); renderLists();
}
function toggleStar(){
  var it = curItem(); if (!it) return;
  var bm = LS.get('bm', []), k = keyOf(it);
  var idx = -1;
  bm.forEach(function(x,i){ if (keyOf(x)===k) idx=i; });
  if (idx>=0){ bm.splice(idx,1); toast('북마크 해제'); }
  else { bm.unshift(it); toast('북마크 추가'); }
  LS.set('bm', bm); renderLists(); syncStar();
}
function syncStar(){
  var it = curItem(); if (!it) return;
  var k = keyOf(it), on = LS.get('bm',[]).some(function(x){ return keyOf(x)===k; });
  $('#btn-star').innerHTML = on ? '&#9733;' : '&#9734;';
  $('#btn-star').classList.toggle('on', on);
}
function renderLists(){
  ['bm','rc'].forEach(function(which){
    var items = LS.get(which==='bm'?'bm':'recent', []);
    var host = $('#'+which);
    if (!items.length){ host.innerHTML='<div class="muted" style="padding:4px 6px">없음</div>';
      return; }
    host.innerHTML = items.map(function(it,i){
      return '<div class="node" data-i="'+i+'"><span class="ic">'+icon(it.kind,false)
        + '</span><span class="nm">'+esc(it.name||it.path||it.id)+'</span></div>'; }).join('');
    host.querySelectorAll('.node').forEach(function(n){
      n.onclick = function(){
        var it = items[+n.dataset.i];
        if (it.mode==='drive') go('drive', it.id); else go('fs', it.root, it.path);
      };
    });
  });
}

/* ---------------- Drive ---------------- */
function setMode(m){
  ST.mode = m;
  document.querySelectorAll('.tab').forEach(function(t){
    t.classList.toggle('on', t.dataset.mode===m); });
  $('#fs-top').hidden = m!=='fs'; $('#tree').hidden = m!=='fs';
  $('#drive-pane').hidden = m!=='drive';
  if (m==='drive') initDrive();
}
function initDrive(){
  var pane = $('#drive-pane');
  if (pane.dataset.init) return;
  pane.dataset.init = '1';
  var st = (CFG && CFG.drive) || {};
  var mounts = (CFG && CFG.driveMounts) || [];
  var mountHint = mounts.length
    ? '<div class="card" style="font-size:12.5px"><b>로컬 마운트 감지</b>'
      + '<div class="muted" style="margin:4px 0 6px">'
      + mounts.map(esc).join('<br>') + '</div>'
      + 'Drive for Desktop 이 동기화한 파일은 <b>API 연결 없이</b> 폴더처럼 볼 수 있습니다. '
      + '<code>--drive</code> 옵션으로 폴더에 추가하세요. Google 문서/스프레드시트'
      + '(.gdoc/.gsheet)는 바로가기 파일이라 아래 API 연결이 필요합니다.</div>'
    : '';
  if (!st.configured){
    pane.innerHTML = '<div class="card" style="font-size:12.5px">'
      + '<b>Google Drive 설정 필요</b>'
      + '<ol style="padding-left:18px;line-height:1.9">'
      + '<li>Google Cloud Console → API 및 서비스 → <b>Drive API 사용 설정</b></li>'
      + '<li>사용자 인증 정보 → OAuth 클라이언트 ID → <b>데스크톱 앱</b></li>'
      + '<li>JSON 다운로드 → <code>'+esc(st.clientFile||'gdrive_client.json')
      + '</code> (스크립트 폴더) 로 저장</li><li>뷰어 재시작 후 [Drive 연결]</li></ol></div>' + mountHint;
    return;
  }
  pane.innerHTML = ''
    + (st.authed ? '' : '<a class="btn" href="/oauth/start" target="_blank">Drive 연결</a>')
    + '<div class="row"><input id="d-url" placeholder="폴더 URL 또는 ID (비우면 내 드라이브)">'
    + '<button class="btn sec" id="d-go">열기</button></div>'
    + '<div id="d-msg" class="muted" style="font-size:12px"></div>'
    + '<div id="d-list" class="tree" style="padding:0"></div>' + mountHint;
  $('#d-go').onclick = function(){ driveList($('#d-url').value.trim() || 'root'); };
  $('#d-url').addEventListener('keydown', function(e){
    if (e.key==='Enter') $('#d-go').click(); });
  var last = LS.get('driveLast', 'root');
  if (st.authed) driveList(last);
}
function driveList(id){
  var host = $('#d-list'), msg = $('#d-msg');
  host.innerHTML = '<div class="muted" style="padding:6px"><i class="spin"></i></div>';
  api('/api/gdrive/list', {id:id}).then(function(d){
    LS.set('driveLast', d.id);
    msg.textContent = '폴더 ' + d.id;
    var files = d.files || [];
    host.innerHTML = files.length ? files.map(function(f,i){
      var isDir = f.mimeType === 'application/vnd.google-apps.folder';
      return '<div class="node" data-i="'+i+'"><span class="ic">'
        + (isDir ? '&#128193;' : driveIcon(f.mimeType)) + '</span>'
        + '<span class="nm">'+esc(f.name)+'</span></div>'; }).join('')
      : '<div class="muted" style="padding:6px">빈 폴더 (또는 권한 없음)</div>';
    host.querySelectorAll('.node').forEach(function(n){
      n.onclick = function(){
        var f = files[+n.dataset.i];
        if (f.mimeType === 'application/vnd.google-apps.folder') driveList(f.id);
        else if (f.mimeType === 'application/vnd.google-apps.shortcut'
                 && f.shortcutDetails) go('drive', f.shortcutDetails.targetId);
        else go('drive', f.id);
      };
    });
  }).catch(function(e){
    host.innerHTML = '';
    msg.innerHTML = '<span class="err">'+esc(e.message)+'</span>'
      + (/인증/.test(e.message) ? ' <a href="/oauth/start" target="_blank">연결하기</a>' : '');
  });
}
function driveIcon(mime){
  mime = mime || '';
  if (mime.indexOf('document')>=0) return '&#128209;';
  if (mime.indexOf('spreadsheet')>=0) return '&#128202;';
  if (mime.indexOf('presentation')>=0) return '&#128200;';
  if (mime.indexOf('pdf')>=0) return '&#128213;';
  if (mime.indexOf('image/')===0) return '&#128444;';
  return '&#128196;';
}

/* ---------------- 자동 갱신 ---------------- */
setInterval(function(){
  if (ST.mode!=='fs' || !ST.doc || ST.doc.kind==='dir' || document.hidden) return;
  if (ST.dirty || $('#split')) return;          // 편집 중에는 덮어쓰지 않는다
  api('/api/stat', {root:ST.root, path:ST.path}).then(function(s){
    if (ST.mtime && s.mtime !== ST.mtime){
      var main = $('#main'), ratio = main.scrollTop / Math.max(1, main.scrollHeight);
      openFs(ST.root, ST.path, {}).then(function(){
        main.scrollTop = ratio * main.scrollHeight; toast('변경 감지 → 갱신됨');
      });
    }
  }).catch(function(){});
}, 2500);

/* ---------------- 헤더/키보드 ---------------- */
function applyTheme(){
  var t = LS.get('theme','auto');
  if (t==='auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', t);
  redrawMermaid();
}
try {                       // auto 테마에서 OS 가 밤낮을 바꾸면 다이어그램도 따라간다
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function(){
    if (LS.get('theme','auto') === 'auto') redrawMermaid();
  });
} catch(e){}
$('#btn-theme').onclick = function(){
  var order = ['auto','light','dark'], t = LS.get('theme','auto');
  t = order[(order.indexOf(t)+1) % 3];
  LS.set('theme', t); applyTheme(); toast('테마: '+t);
};
var DOCW = {normal:'기본 960px', wide:'넓게 1440px', full:'창 전체'};
function applyDocWidth(){
  var w = LS.get('docWidth','normal');
  if (!DOCW[w]) w = 'normal';
  if (w==='normal') document.body.removeAttribute('data-docw');
  else document.body.setAttribute('data-docw', w);
  var b = $('#btn-width');
  b.classList.toggle('on', w!=='normal');
  b.title = '본문 폭: '+DOCW[w]+' [w]';
}
$('#btn-width').onclick = function(){
  var order = ['normal','wide','full'], w = LS.get('docWidth','normal');
  w = order[(order.indexOf(w)+1) % 3];
  LS.set('docWidth', w); applyDocWidth(); toast('본문 폭: '+DOCW[w]);
};
$('#btn-side').onclick = function(){
  document.body.classList.toggle('no-side');
  LS.set('noSide', document.body.classList.contains('no-side'));
};
$('#btn-toc').onclick = function(){
  document.body.classList.toggle('no-toc');
  LS.set('noToc', document.body.classList.contains('no-toc'));
};
$('#btn-tree-reload').onclick = refreshTree;
$('#btn-reload').onclick = function(){
  if (ST.mode==='fs' && ST.doc) openFs(ST.root, ST.path, {});
  else if (ST.mode==='drive') openDrive(ST.path, {});
  if (ST.mode==='fs'){ ST.tree={}; buildTree(); if (ST.path) reveal(ST.root, ST.path); }
  toast('새로고침');
};
$('#btn-star').onclick = toggleStar;
$('#btn-scope').onclick = function(){
  ST.searchScope = (ST.searchScope==='root') ? 'all' : 'root';
  var b = $('#btn-scope');
  b.textContent = ST.searchScope==='root' ? '현재' : '전체';
  b.title = ST.searchScope==='root' ? '검색 범위: 현재 폴더' : '검색 범위: 전체 폴더';
  b.classList.toggle('on', ST.searchScope==='root');
  LS.set('scope', ST.searchScope);
  if ($('#q').value.trim()) doSearch($('#q').value.trim());
};
$('#filter').addEventListener('input', applyFilter);
$('#filter').addEventListener('keydown', function(e){
  if (e.key==='Escape'){ $('#filter').value=''; applyFilter(); $('#filter').blur(); }
});
document.querySelectorAll('.tab').forEach(function(t){
  t.onclick = function(){ setMode(t.dataset.mode); };
});

document.addEventListener('keydown', function(e){
  if (e.target.tagName==='INPUT' || e.target.tagName==='SELECT'
      || e.target.tagName==='TEXTAREA') return;        // 편집기 입력 보호
  if (e.metaKey||e.ctrlKey||e.altKey) return;
  if (e.key==='/'){ e.preventDefault(); $('#q').focus(); $('#q').select(); }
  else if (e.key==='Escape'){ $('#results').hidden = true; }
  else if (e.key==='b'){ $('#btn-side').click(); }
  else if (e.key==='t'){ $('#btn-toc').click(); }
  else if (e.key==='w'){ $('#btn-width').click(); }
  else if (e.key==='r'){ $('#btn-reload').click(); }
  else if (e.key==='s'){ toggleStar(); }
});
/* 사이드바 너비 조절 */
(function(){
  var rz = $('#resizer'), side = $('#side'), MIN = 170, MAX = 720, DEF = 290;
  var w = LS.get('sideW', DEF);
  side.style.width = Math.min(MAX, Math.max(MIN, w)) + 'px';
  var startX = 0, startW = 0, dragging = false;
  function down(e){
    dragging = true; startX = e.clientX; startW = side.offsetWidth;
    rz.classList.add('dragging'); document.body.classList.add('resizing');
    if (rz.setPointerCapture && e.pointerId != null) rz.setPointerCapture(e.pointerId);
  }
  function move(e){
    if (!dragging) return;
    var nw = Math.min(MAX, Math.max(MIN, startW + (e.clientX - startX)));
    side.style.width = nw + 'px';
  }
  function up(){
    if (!dragging) return;
    dragging = false; rz.classList.remove('dragging');
    document.body.classList.remove('resizing');
    LS.set('sideW', side.offsetWidth);
  }
  rz.addEventListener('pointerdown', down);
  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', up);
  window.addEventListener('pointercancel', up);
  rz.addEventListener('dblclick', function(){
    side.style.width = DEF + 'px'; LS.set('sideW', DEF); });
})();

window.addEventListener('beforeunload', function(e){
  if (ST.dirty){ e.preventDefault(); e.returnValue = ''; }
});
window.addEventListener('hashchange', onRoute);

function showWelcome(){
  crumb(null); setToc([]);
  var roots = (CFG.roots||[]).map(function(r){
    return '<div class="node" data-r="'+esc(r.id)+'"><span class="ic">&#128193;</span>'
      + '<span class="nm">'+esc(r.name)+'</span><span class="sz">'+esc(r.path)
      + '</span></div>'; }).join('');
  $('#doc').innerHTML = '<article class="md"><h1>docs viewer</h1>'
    + '<p class="muted">왼쪽 트리에서 문서를 고르거나, <kbd>/</kbd> 로 전체 검색하세요.</p>'
    + '<h3>등록된 폴더</h3><div class="card" style="padding:8px">'+roots+'</div>'
    + '<h3>단축키</h3><p><kbd>/</kbd> 검색 · <kbd>b</kbd> 사이드바 · <kbd>t</kbd> 목차 · '
    + '<kbd>r</kbd> 새로고침 · <kbd>s</kbd> 북마크 · <kbd>esc</kbd> 닫기</p>'
    + '<p class="muted">md · html · 텍스트/코드 · 이미지 · PDF · CSV'
    + (CFG.soffice ? ' · docx/xlsx/pptx' : '')
    + (CFG.drive && CFG.drive.configured ? ' · Google Drive' : '') + ' 지원</p></article>';
  $('#doc').querySelectorAll('.node').forEach(function(n){
    n.onclick = function(){ go('fs', n.dataset.r, ''); }; });
}

/* ---------------- 부팅 ---------------- */
applyTheme();
applyDocWidth();
if (LS.get('noSide',false)) document.body.classList.add('no-side');
if (LS.get('noToc',false)) document.body.classList.add('no-toc');
ST.expanded = LS.get('expanded', {});
ST.searchScope = LS.get('scope', 'all');
$('#btn-scope').textContent = ST.searchScope==='root' ? '현재' : '전체';
$('#btn-scope').classList.toggle('on', ST.searchScope==='root');
renderLists();
api('/api/config').then(function(c){
  CFG = c;
  if (!c.driveTab){                     // OAuth 미설정이면 Drive 탭 자체를 감춘다
    var tabs = document.querySelector('.tabs');
    if (tabs) tabs.hidden = true;
    var dt = document.querySelector('.tab[data-mode="drive"]');
    if (dt) dt.hidden = true;
  }
  buildTree();
  if (routeOf()){ onRoute(); }
  else { ST.root = ((c.roots||[])[0]||{}).id || null; showWelcome(); }
}).catch(function(e){
  $('#doc').innerHTML = '<div class="card err">설정을 불러올 수 없습니다: '
    + esc(e.message)+'</div>';
});
</script>
</body>
</html>
"""


# ---------------------------------------------------------------- 실행부
def load_config_file():
    f = HOME / "config.json"
    try:
        return json.loads(f.read_text("utf-8"))
    except Exception:
        return {}


def make_roots(paths):
    roots, seen = [], set()
    for raw in paths:
        given = Path(os.path.expanduser(str(raw)))
        p = Path(os.path.realpath(str(given)))
        if not p.is_dir():
            sys.stderr.write("  ! 폴더가 아니라 건너뜁니다: %s\n" % raw)
            continue
        if str(p) in seen:
            continue
        seen.add(str(p))
        # 표시 이름은 사용자가 적어준 경로 기준 (심볼릭 링크 대상 이름이 아니라)
        name = given.name or p.name or str(p)
        cloud = False
        if name.startswith("GoogleDrive-"):
            name = "Drive (%s)" % name.split("-", 1)[1]
            cloud = True
        elif "/CloudStorage/" in str(p) or name in ("Google Drive", "GoogleDrive"):
            name = "Drive"
            cloud = True
        if any(r["name"] == name for r in roots):
            name = "%s (%s)" % (name, p.parent.name)
        roots.append({"id": "r%d" % len(roots), "name": name, "path": p,
                      "cloud": cloud, "lazy": cloud})
    return roots


def local_ips():
    """표시용: 이 머신의 IPv4 주소 목록 (루프백 제외)."""
    ips = []

    def add(ip):
        if ip and not ip.startswith("127.") and ip not in ips:
            ips.append(ip)

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 53))          # 실제 전송 없음, 기본 경로 IP 확인용
        add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        add_all = socket.gethostbyname_ex(socket.gethostname())[2]
        for ip in add_all:
            add(ip)
    except OSError:
        pass
    for cmd in (["ifconfig"], ["ip", "-4", "addr"]):
        exe = shutil.which(cmd[0])
        if not exe:
            continue
        try:
            out = subprocess.run([exe] + cmd[1:], stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, timeout=5).stdout.decode(
                                     "utf-8", "replace")
        except Exception:
            continue
        for m in re.finditer(r"inet (?:addr:)?(\d+\.\d+\.\d+\.\d+)", out):
            add(m.group(1))
        break
    return ips


def pick_port(host, want, fixed):
    for off in range(0, 1 if fixed else 25):
        port = want + off
        s = socket.socket()
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind((host, port))
            return port
        except OSError:
            continue
        finally:
            s.close()
    raise SystemExit("포트를 열 수 없습니다: %s:%d" % (host, want))


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="docs_viewer.py",
        description="로컬 문서 뷰어 (md/html/코드/이미지/PDF + Google Drive)")
    ap.add_argument("roots", nargs="*", help="공개할 폴더 (여러 개 가능, 기본: 현재 폴더)")
    ap.add_argument("-p", "--port", type=int, default=None, help="포트 (기본 %d)" % DEFAULT_PORT)
    ap.add_argument("--host", default=None, help="바인딩 주소 (기본 127.0.0.1)")
    ap.add_argument("--lan", action="store_true",
                    help="같은 네트워크의 다른 기기에서도 접속 허용 (--host 0.0.0.0 과 동일)")
    ap.add_argument("-n", "--no-browser", action="store_true", help="브라우저 자동 실행 안 함")
    ap.add_argument("--allow-edit", action="store_true",
                    help="분할 편집기에서 파일 저장 허용 (기본은 읽기 전용)")
    ap.add_argument("--drive", action="store_true",
                    help="Google Drive for Desktop 마운트를 폴더로 자동 추가")
    ap.add_argument("--no-token", action="store_true",
                    help="토큰 없이 접속 허용 (127.0.0.1 전용일 때만, 혼자 쓰는 머신 권장)")
    ap.add_argument("--hidden", action="store_true", help="숨김 파일도 표시")
    ap.add_argument("--md-unsafe", action="store_true",
                    help="마크다운 안의 raw HTML 을 살균 없이 렌더 (신뢰하는 문서에만)")
    ap.add_argument("--no-mermaid", action="store_true",
                    help="mermaid 코드 블록을 다이어그램으로 그리지 않음 (다운로드도 안 함)")
    ap.add_argument("-v", "--verbose", action="store_true", help="요청 로그 출력")
    ap.add_argument("--version", action="version", version="%s %s" % (APP_TITLE, VERSION))
    args = ap.parse_args(argv)

    conf = load_config_file()
    paths = list(args.roots or conf.get("roots") or [os.getcwd()])
    if args.drive or conf.get("drive"):
        for m in drive_mounts():
            if str(m) not in [str(Path(os.path.expanduser(str(x)))) for x in paths]:
                paths.append(str(m))
    CFG.roots = make_roots(paths)
    if not CFG.roots:
        raise SystemExit("표시할 폴더가 없습니다.")
    CFG.host = args.host or ("0.0.0.0" if (args.lan or conf.get("lan"))
                             else conf.get("host") or "127.0.0.1")
    CFG.lan = CFG.host not in LOOPBACK_NAMES
    try:
        hn = socket.gethostname().lower().rstrip(".")
        CFG.host_names = {hn, hn.split(".")[0], hn.split(".")[0] + ".local"}
    except OSError:
        CFG.host_names = set()
    CFG.drive_tab = conf.get("drive_tab")      # 없으면 None(자동)
    CFG.allow_edit = bool(args.allow_edit or conf.get("allow_edit"))
    CFG.md_unsafe = bool(args.md_unsafe or conf.get("md_unsafe"))
    CFG.show_hidden = bool(args.hidden or conf.get("show_hidden"))
    CFG.mermaid = not (args.no_mermaid or conf.get("no_mermaid"))
    CFG.soffice = find_soffice()
    CFG.oauth_state = secrets.token_urlsafe(12)
    no_token = bool(args.no_token or conf.get("no_token"))
    if no_token and CFG.lan:
        raise SystemExit("--no-token 은 루프백 전용입니다. LAN 노출 시에는 토큰이 필요합니다.")
    CFG.token = "" if no_token else secrets.token_urlsafe(24)
    if args.verbose:
        os.environ["DOCS_VIEWER_VERBOSE"] = "1"
    HOME.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    mimetypes.add_type("text/markdown", ".md")
    mimetypes.add_type("image/svg+xml", ".svg")

    want = args.port or int(conf.get("port") or DEFAULT_PORT)
    CFG.port = pick_port(CFG.host, want, args.port is not None)

    disp_host = "127.0.0.1" if CFG.host in ("0.0.0.0", "::") else CFG.host
    url = "http://%s:%d/%s" % (disp_host, CFG.port,
                               ("?t=%s" % CFG.token) if CFG.token else "")
    httpd = Server((CFG.host, CFG.port), Handler)

    line = "-" * 62
    print(line)
    print("  %s %s" % (APP_TITLE, VERSION))
    print(line)
    for r in CFG.roots:
        tag = " (클라우드: 내려온 파일만 내용검색)" if r.get("lazy") else ""
        print("  [%s] %-22s %s%s" % (r["id"], r["name"][:22], r["path"], tag))
    if not any(r.get("cloud") for r in CFG.roots):
        for m in drive_mounts():
            print("  * Drive 마운트 발견: %s" % m)
            print("    --drive 를 주면 폴더로 자동 추가됩니다.")
    print("  드라이브 : %s" % ("연결됨" if DRIVE.status()["authed"]
                            else ("설정됨(미인증)" if DRIVE.status()["configured"]
                                  else "미설정 - %s" % DRIVE.client_file)))
    print("  office  : %s" % (CFG.soffice or "soffice 없음 (docx/xlsx 미리보기 불가)"))
    print("  편집    : %s" % ("허용 (분할 편집기에서 저장 가능)" if CFG.allow_edit
                            else "읽기 전용 (--allow-edit 로 활성화)"))
    if not CFG.mermaid:
        print("  mermaid : 꺼짐 (--no-mermaid)")
    elif _mermaid_local():
        print("  mermaid : %s" % _mermaid_local())
    else:
        print("  mermaid : 처음 쓸 때 %s 에서 1회 다운로드" % MERMAID_URL.split("/")[2])
    print(line)
    print("  %s" % url)
    if CFG.lan:
        for ip in local_ips():
            print("  http://%s:%d/?t=%s" % (ip, CFG.port, CFG.token))
    if not CFG.token:
        print("  http://localhost:%d/            (토큰 없음 - 루프백 전용)" % CFG.port)
    print("  (Ctrl+C 로 종료)")
    print(line)
    if CFG.lan:
        print("  ** LAN 노출 모드: 같은 네트워크의 다른 기기에서 이 문서들을 볼 수 있습니다.")
        print("     토큰이 붙은 URL 만 유효하고 읽기 전용이지만, 평문 HTTP 이므로")
        print("     신뢰하는 네트워크에서만 사용하세요.")
        print(line)

    if not args.no_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  종료합니다.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
