# Google style audit

Audited on 2026-10-06 for Task 14, against the approved FastAPI migration design.
The three pages remain native HTML, CSS and JavaScript. Browser scripts are ES
modules with no transpiler or runtime package dependency. npm tools are for
development checks; the remaining migration baseline server is removed at cutover.

Sources: [Google JavaScript guide](https://google.github.io/styleguide/jsguide.html)
and [Google HTML/CSS guide](https://google.github.io/styleguide/htmlcssguide.html).
The JavaScript guide is retired; retaining JavaScript is an approved project choice.

## Automatic coverage

`npm run lint` runs ESLint, Stylelint and HTML Validate on every public JS, CSS and
HTML file. Each command is also available as `lint:js`, `lint:css` and `lint:html`.
The configurations contain no file-wide rule suppressions.

| Guide requirement | Check |
| --- | --- |
| JS block indentation, braces, semicolons | ESLint two-space block/literal rules; `curly` |
| JS continuation indentation (§4.5.2) | Four-space call/parameter/member offsets plus local `google/continuation-indent` minimum for assignments, declarations, branches, operands and expression arrows |
| JS single quotes, spacing, trailing commas | ESLint quotes, spacing, comma rules |
| JS 80 columns, one statement per line | ESLint max length and statement rules |
| JS modern declarations and callbacks | `no-var`, `prefer-const`, `prefer-arrow-callback` |
| JS correctness | Recommended ESLint rules; strict equality |
| CSS indentation and rule separation | Stylelint indentation and blank-line rules |
| CSS declaration order | Alphabetical property rule |
| CSS punctuation, single quotes, selector spacing | Stylistic Stylelint rules, `string-quotes: single` |
| CSS short hex, zero units, mandatory leading zeros | Stylelint hex and zero rules, `number-leading-zero: always` |
| CSS syntax and duplication | Property, unit, color and duplicate checks |
| CSS ID selectors and important declarations | Stylelint, with the local exceptions below |
| HTML document structure and accessibility | HTML Validate recommended rules |
| HTML quotes, doctype, void elements, whitespace | Explicit HTML Validate rules |

## Manual coverage

- UTF-8, lowercase filenames, descriptive names, two-space blocks/literals and
  continuation lines at least four spaces beyond their statement reviewed.
- Module boundaries, lowerCamelCase functions, JSDoc parameter and return types
  reviewed. Dynamic DOM/API objects retain the established field names.
- Native controls, heading hierarchy, image descriptions, keyboard upload and
  explicit API-key label reviewed. Inline script and presentation styles absent.
- CSS selectors, shorthand, source order, grouped selectors and media queries
  reviewed. Quote and number spelling changed; computed values, dimensions and
  responsive breakpoints retained.
- User and provider text uses `textContent`; the existing module-card HTML contains
  only fixed markup. API fields, navigation addresses and visible copy retained.
- `settings.js` fetches `providers.json`; presets contain no secrets. SK stays in
  the input only until save and is never written to browser storage. The service
  public role responses contain `hasKey`, never `apiKey`.

## Narrow exceptions

| Rule | Location and reason |
| --- | --- |
| JS 80-column limit | Literal strings, templates, URLs and regular expressions may remain unbroken; UI copy and endpoint patterns are kept readable and identical. Executable expression lines remain checked. |
| CSS avoids IDs | Three existing selectors: `jobStatus`, `deleteJob`, `jobList`. Local comments retain their existing specificity and DOM contract. |
| CSS avoids `!important` | `[hidden]` must override display rules; two `result-settings` declarations preserve the existing header override. Each declaration has a local exception. |
| Native button preferred | `uploadZone` contains generated remove/add buttons. Its existing focusable container and Enter/Space listeners avoid nested buttons. One local HTML exception. |
| Password autocomplete | API credentials retain `autocomplete="off"` as the existing autofill opt-out. One local HTML exception; browser autofill behavior is not claimed to guarantee secret storage policy. |

These exceptions preserve existing behavior and are documented at the exact code
site. No validator is globally disabled to accommodate legacy code.

## Verification evidence

Task evidence lives under
`.superpowers/sdd/2026-10-05-fastapi-google-style-migration/task-14-evidence/`.

| Verification | Result |
| --- | --- |
| Old-source lint | ESLint 323, Stylelint 1,565, HTML Validate 14 errors |
| Final lint | All three checks pass |
| Three pages, 390px and 1280px | Six baseline/after screenshot pairs are pixel identical |
| Browser interactions | Upload, settings, AI-assist failure/success, polling, retry, cancel, delete and downloads pass |
| Python suite | 219 tests pass; standalone contract check retained |
| Preserved Node comparison suite | 15 tests pass during migration |

The browser server used port 8768, a disposable temporary data directory and an
injected `httpx.MockTransport`. No real credential or external provider request was
used. Provider failures left the user's original copy unchanged; successful copy
remained editable. Browser results record actual polling states, retry attempts
`[1, 2]`, and a ZIP containing both completed images plus its manifest and copy list.
Screenshots of uploaded, configured, partial and completed states were visually
reviewed at both viewport widths. Real provider capability remains a separate
account-level validation.

Review round 1 corrected CSS quoting/leading-zero settings and strengthened JS
continuation checks. The final corrected rules reject the prior commit with 58 JS
and nine CSS errors.
The dedicated minimum rule covers expression tokens that the stylistic rule
deliberately ignores. Conditional expressions, leaf property values and expression
arrow bodies delegate their continuation minimum to that rule; literal contents
and block bodies keep the stylistic two-space check. Invalid +2 continuations and
valid mixed +2 blocks/+4 continuations were exercised independently.

Review round 2 narrowed the property-value exemption to `Literal` and `Identifier`
leaves. Nested object/array contents and call arguments remain entirely under the
stylistic indentation rule. The reviewer's two exact negative snippets and
standard multiline negative/positive counterparts are covered by the rule probes.

## Python and unified gate (Task 15)

The locked Python 3.11 environment checks all `backend/` and `tests/` files with
Pyink at 80 columns. Pylint checks `backend/` against the unchanged Google
configuration pinned in Task 1 at `config/google.pylintrc`. The unified
`scripts/check.ps1` runs these checks, the complete pytest suite and all frontend
validators in order. Native failures retain their exit codes; missing commands
and PowerShell errors exit nonzero. The script resolves its project directory
relative to its own location.

Manual review covered module imports, snake_case internal names, docstrings,
explicit exception boundaries and preservation of public camelCase data.
Existing local exceptions in the backend remain confined to replaying a bounded
Starlette body (`protected-access`) and catching unexpected errors at disk,
worker and module isolation boundaries (`broad-exception-caught`). No rule or
configuration was disabled or changed in this task. The test HTTP handler uses
the standard-library override name `do_POST`, with one local `invalid-name`
annotation; it is test infrastructure, outside the backend Pylint target.

The gate is itself executed in five Windows test cases: native uv/npm command
doubles fail Pyink, Pylint, pytest and frontend lint individually with exit 17,
and an all-success case completes all four commands with exit 0. Assertions
check actual subprocess exit codes and command order, including stopping after
the failing command. Full run evidence is in [verification.md](verification.md).
