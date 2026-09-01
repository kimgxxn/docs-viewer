# 서드파티 고지

`docs_viewer.py` 본체는 파이썬 표준 라이브러리만 사용하며, 외부 코드를 포함하지
않습니다 (마크다운 렌더러·문법 하이라이터 모두 직접 구현).

배포판에 포함되는 서드파티 파일은 아래 하나뿐입니다.

## assets/mermaid.min.js

- 프로젝트: [mermaid](https://github.com/mermaid-js/mermaid) 11.17.2
- 라이선스: MIT — Copyright (c) 2014 - 2022 Knut Sveidqvist
- 출처: `https://cdn.jsdelivr.net/npm/mermaid@11.17.2/dist/mermaid.min.js`
- 용도: ```` ```mermaid ```` 코드펜스를 브라우저에서 다이어그램으로 렌더

이 파일은 번들이라 아래 라이브러리들이 함께 들어 있습니다. 각자의 라이선스를 따릅니다.

- **DOMPurify** — Apache License 2.0 또는 Mozilla Public License 2.0
  (c) Cure53 and other contributors
- 그 밖에 d3 등 mermaid 의 의존성 (각 프로젝트의 MIT / ISC / BSD 라이선스)

`assets/mermaid.min.js` 를 지우면 이 배포판에는 서드파티 코드가 전혀 남지 않습니다.
그 경우 다이어그램은 처음 볼 때 jsdelivr 에서 한 번 내려받아 `cache/` 에 두고 쓰며,
`--no-mermaid` 로 아예 끄면 코드 블록으로만 표시됩니다.
