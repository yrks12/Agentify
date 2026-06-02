# Security Policy

## Trust model — read this before running recipes

Agentify recipes are **executable**. A `<slug>.tools.json` file is not passive
data: replaying it drives a real browser (navigates, clicks, types) and, via the
`js_extract` op, **runs arbitrary JavaScript in the page context**. Treat a
recipe the same way you would treat a shell script.

- **Only run recipes you generated yourself or obtained from a source you
  trust.** A malicious recipe can exfiltrate page data, perform actions as the
  logged-in user, or abuse any site you point it at.
- **Review `js_extract` steps before replaying a third-party recipe.** The
  `expr` field is raw JS executed with `page.evaluate`.
- **`map` drives sites with an LLM.** Don't map sites behind authentication you
  don't own, and don't map sites whose Terms of Service forbid automation. See
  "Responsible use" in the README.

## Secrets

- Your `OPENAI_API_KEY` lives in `source/.env`, which is git-ignored. Never
  commit it. `source/.env.example` ships a placeholder only.
- Agentify does not log API keys. If you add logging, scrub credentials.

## Supported versions

Agentify is pre-1.0; only the latest `main` is supported. Pin a commit if you
need stability.

## Reporting a vulnerability

Please **do not open a public issue** for security problems. Instead, use
GitHub's private vulnerability reporting:

> Repo → **Security** tab → **Report a vulnerability**

Include a description, reproduction steps, and impact. We aim to acknowledge
within 7 days. Coordinated disclosure is appreciated — give us a reasonable
window to ship a fix before public disclosure.
