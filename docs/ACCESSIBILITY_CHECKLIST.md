# Accessibility checklist (WCAG 2.1 AA target)

Use for client-facing and advisor-facing templates before release.

## Per page

- [ ] Page has a single `<h1>`; heading hierarchy is logical
- [ ] All form inputs have `<label>` or `aria-label`
- [ ] Focus order matches visual order; focus visible on keyboard tab
- [ ] Color contrast ≥ 4.5:1 for body text (Bootstrap primary on white checked)
- [ ] Tables use `<th scope="col">` where applicable
- [ ] Icons paired with text or `aria-hidden="true"` + visible label
- [ ] Error messages linked with `aria-describedby` on fields
- [ ] No information conveyed by color alone (use icon + text for status)

## Finance-specific

- [ ] Numbers use `number_format` / consistent locale
- [ ] Negative values clear (parentheses or minus, not red-only)
- [ ] Date fields ISO in API; localized display in templates

## Mobile web

- [ ] Tap targets ≥ 44×44 px (`mobile-bottom-nav`, primary buttons)
- [ ] `viewport` meta present (`base.html`)
- [ ] No horizontal scroll on 375px width

## 2FA / auth

- [ ] Backup codes printable; not only in image
- [ ] Session timeout documented for advisors (2h in config)

## Sign-off

| Role | Name | Date |
|------|------|------|
| Dev | | |
| QA | | |
