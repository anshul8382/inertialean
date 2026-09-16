# Dependency vulnerability audit

Generated: 2026-09-15T17:26:07.544684+00:00
Python runtime: 3.11.9
Scanners: `pip-audit` (Python) + `npm audit --omit=dev` (Capacitor shell)

## Summary

| Source | Available | Findings |
|--------|-----------|----------|
| Python (`requirements.txt`) | yes | 14 |
| Capacitor shell (`apps/capacitor-shell/`) | yes | 0 |

## Python (`pip-audit`)

**Severity counts:** unknown=14

| Package | Version | Advisory | Severity | Fix versions | Note |
|---------|---------|----------|----------|--------------|------|
| click | 8.1.7 | PYSEC-2026-2132 | unknown | 8.3.3 | Pallets Click, versions 8.3.2 and below, contain a command injection vulnerability in the click.edit() function, allowing attackers to pass arbitrary OS commands from an unprivileged account. |
| httplib2 | 0.22.0 | PYSEC-2026-3444 | unknown | 0.32.0 | httplib2 is a comprehensive HTTP client library for Python. Prior to 0.32.0, httplib2 performs unbounded decompression of HTTP response bodies encoded with Content-Encoding: gzip or deflate in _decompressContent in httplib2/init.py, allowin |
| httplib2 | 0.22.0 | PYSEC-2026-3444 | unknown | 0.32.0 | ### Summary  The `httplib2` HTTP client library performs unbounded decompression of HTTP response bodies encoded with `Content-Encoding: gzip` or `deflate`. A malicious or compromised HTTP server can return a small compressed payload (appro |
| pyasn1 | 0.6.3 | PYSEC-2026-3456 | unknown | 0.6.4 | ### Impact The BER/CER/DER decoders process OBJECT IDENTIFIER and RELATIVE-OID values in quadratic time relative to the number of arcs. A small crafted payload (tens of kilobytes) containing an OID with many arcs consumes seconds of CPU per |
| pyasn1 | 0.6.3 | PYSEC-2026-3457 | unknown | 0.6.4 | ### Impact The univ.Real type converted its (mantissa, base, exponent) value to a Python float using exact big-integer exponentiation. A BER/CER/DER-encoded REAL value only a few bytes long can carry a very large exponent, causing this comp |
| pyasn1 | 0.6.3 | PYSEC-2026-3456 | unknown | 0.6.4 | pyasn1 is a generic ASN.1 library for Python. Prior to 0.6.4, the BER, CER, and DER decoders process OBJECT IDENTIFIER and RELATIVE-OID values in quadratic time relative to the number of arcs, so a small crafted payload containing an OID wi |
| pyasn1 | 0.6.3 | PYSEC-2026-3457 | unknown | 0.6.4 | pyasn1 is a generic ASN.1 library for Python. Prior to 0.6.4, the univ.Real type converted its mantissa, base, and exponent value to a Python float using exact big-integer exponentiation. A BER, CER, or DER encoded REAL value only a few byt |
| pyasn1 | 0.6.3 | PYSEC-2026-3455 | unknown | 0.6.4 | pyasn1 is a generic ASN.1 library for Python. Prior to 0.6.4, the BER decoder shared by the CER and DER codecs parses long-form tags by accumulating continuation octets without an upper bound on the tag ID size, allowing a crafted input to  |
| pyasn1 | 0.6.3 | PYSEC-2026-3455 | unknown | 0.6.4 | ### Impact The BER decoder (shared by the CER and DER codecs) parses long-form tags by accumulating continuation octets in a loop with no upper bound on the size of the tag ID. A crafted input can force the decoder to build an arbitrarily l |
| pypdf2 | 3.0.1 | PYSEC-2026-1835 | unknown | 3.9.0 | ### Impact An attacker who uses this vulnerability can craft a PDF which leads to an infinite loop if `__parse_content_stream` is executed. This infinite loop blocks the current process and can utilize a single core of the CPU by 100%. It d |
| soupsieve | 2.7 | PYSEC-2026-3072 | unknown | 2.8.4 | ### Summary  The CSS selector parser in soupsieve (the CSS selector engine for Beautiful Soup 4) contains a regular expression vulnerable to catastrophic backtracking. When processing an attribute selector with an unterminated quoted value, |
| soupsieve | 2.7 | PYSEC-2026-3071 | unknown | 2.8.4 | ### Summary  The CSS selector parser in soupsieve (the CSS selector engine for Beautiful Soup 4) allocates unbounded memory when compiling large comma-separated selector lists. An attacker who can supply a crafted CSS selector string to `so |
| soupsieve | 2.7 | PYSEC-2026-3072 | unknown | 2.8.4 | ### Summary  The CSS selector parser in soupsieve (the CSS selector engine for Beautiful Soup 4) contains a regular expression vulnerable to catastrophic backtracking. When processing an attribute selector with an unterminated quoted value, |
| soupsieve | 2.7 | PYSEC-2026-3071 | unknown | 2.8.4 | ### Summary  The CSS selector parser in soupsieve (the CSS selector engine for Beautiful Soup 4) allocates unbounded memory when compiling large comma-separated selector lists. An attacker who can supply a crafted CSS selector string to `so |

## Capacitor shell (`npm audit`)

No known vulnerabilities in production dependencies.

---

**Triage:** upgrade critical/high vulnerabilities in a focused PR. Run the app's full pytest suite before deploy.

## Python version note

This audit ran on Python **3.11.9**. Several upstream advisories ship the fix only in
package versions that require **Python 3.10+** (Pillow 12.x, urllib3 2.7+, requests 2.33+,
python-dotenv 1.2.2+, weasyprint 68+). `requirements.txt` uses conditional pins so a Python
3.10+ environment (recommended for production) installs the clean versions automatically.
Local dev on Python 3.9 will show these as residual findings.

Re-run:
```bash
python3 scripts/run_dependency_audit.py --write-report
```
