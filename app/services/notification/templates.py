from __future__ import annotations

import base64
import html
from pathlib import Path
from typing import Iterable

from app.config.settings import settings

COSMOZPAY_PRIMARY = "#5B21B6"
COSMOZPAY_PRIMARY_DARK = "#4C1D95"
COSMOZPAY_ACCENT = "#8B5CF6"
COSMOZPAY_BACKGROUND = "#F6F2FF"
COSMOZPAY_TEXT = "#1F1B33"
COSMOZPAY_MUTED = "#6B7280"
COSMOZPAY_BORDER = "#EDEDED"
COSMOZPAY_SURFACE = "#FFFFFF"
COSMOZPAY_SUCCESS = "#22C55E"


def _escape(value: object | None) -> str:
    if value is None:
        return ""
    return html.escape(str(value), quote=True)


def _brand_logo_data_uri() -> str:
    candidates = [
        "CosmozPaylogo2.jpeg",
        "Cosmozpay-main-logo.png",
        "Cosmozpaylogo.jpeg",
        "Cosmozpaylogo.png",
    ]
    frontend_root = Path(__file__).resolve().parents[4] / "Cosmozpay-Frontend" / "public"
    for filename in candidates:
        path = frontend_root / filename
        if not path.exists():
            continue
        suffix = path.suffix.lower().lstrip(".")
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        return f"data:image/{suffix};base64,{encoded}"
    return ""


def render_cosmozpay_email(
    *,
    subject: str,
    title: str,
    greeting: str | None = None,
    body_lines: Iterable[str] | None = None,
    cta_text: str | None = None,
    cta_url: str | None = None,
    footer_note: str | None = None,
    support_email: str | None = None,
    logo_data_uri: str | None = None,
) -> tuple[str, str]:
    safe_subject = _escape(subject or "CosmozPay")
    safe_title = _escape(title or "CosmozPay")
    safe_greeting = _escape(greeting) if greeting else ""
    safe_cta_text = _escape(cta_text) if cta_text else ""
    safe_cta_url = _escape(cta_url) if cta_url else ""
    safe_footer = _escape(footer_note or "Thank you for using CosmozPay.")
    safe_support = _escape(support_email) if support_email else ""

    lines = [str(line).strip() for line in (body_lines or []) if str(line).strip()]
    safe_lines = [_escape(line) for line in lines]
    logo_uri = logo_data_uri or _brand_logo_data_uri()

    hero = ""
    if safe_greeting:
        hero = f"<p style=\"margin:0 0 18px; font-size:15px; line-height:24px; color:{COSMOZPAY_MUTED};\">{safe_greeting}</p>"

    body_markup = "".join(
        f"<p style=\"margin:0 0 14px; font-size:16px; line-height:26px; color:{COSMOZPAY_TEXT};\">{line}</p>"
        for line in safe_lines
    ) or "<p style=\"margin:0; font-size:16px; line-height:26px; color:{COSMOZPAY_TEXT};\">Thank you for choosing CosmozPay.</p>"

    if safe_cta_url:
        button_text = safe_cta_text or "Get Started"
        cta_block = (
            "<table role=\"presentation\" cellpadding=\"0\" cellspacing=\"0\" border=\"0\" style=\"border-collapse:separate; margin:18px 0 24px;\">"
            f"<tr><td align=\"center\" bgcolor=\"{COSMOZPAY_PRIMARY}\" style=\"border-radius:10px;\">"
            f"<a href=\"{safe_cta_url}\" style=\"display:inline-block; padding:14px 24px; font-family:Arial, Helvetica, sans-serif; font-size:15px; font-weight:700; color:#ffffff; text-decoration:none;\">{button_text}</a>"
            "</td></tr></table>"
        )
    else:
        cta_block = ""

    support_html = (
        f"<p style=\"margin:0; font-size:12px; line-height:20px; color:{COSMOZPAY_MUTED};\">Need help? Contact <a href=\"mailto:{safe_support}\" style=\"color:{COSMOZPAY_PRIMARY}; text-decoration:none;\">{safe_support}</a></p>"
        if safe_support
        else ""
    )

    logo_html = ""
    if logo_uri:
        logo_html = (
            f"<img src=\"{logo_uri}\" alt=\"CosmozPay logo\" width=\"160\" style=\"display:block; width:160px; max-width:160px; height:auto; margin:0 auto 18px; border:0; outline:none; text-decoration:none;\" />"
        )

    html_document = f"""<!doctype html>
<html lang="en">
  <head>
    <meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{safe_subject}</title>
  </head>
  <body style="margin:0; padding:0; background-color:#f3f4f6; font-family:Arial, Helvetica, sans-serif; color:{COSMOZPAY_TEXT};">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="width:100%; background-color:#f3f4f6; border-collapse:collapse;">
      <tr>
        <td align="center" style="padding:32px 16px;">
          <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="max-width:620px; width:100%; background-color:{COSMOZPAY_SURFACE}; border-collapse:collapse; border:1px solid {COSMOZPAY_BORDER}; border-radius:18px; overflow:hidden;">
            <tr>
              <td style="padding:28px 28px 18px; background-color:{COSMOZPAY_BACKGROUND}; border-bottom:1px solid {COSMOZPAY_BORDER}; text-align:center;">
                {logo_html}
                <div style="font-size:12px; letter-spacing:1.5px; text-transform:uppercase; color:{COSMOZPAY_MUTED}; font-weight:700;">CosmozPay</div>
              </td>
            </tr>
            <tr>
              <td style="padding:28px 28px 12px;">
                {hero}
                <h1 style="margin:0 0 12px; font-size:30px; line-height:38px; color:{COSMOZPAY_TEXT}; font-weight:700;">{safe_title}</h1>
                {body_markup}
                {cta_block}
              </td>
            </tr>
            <tr>
              <td style="padding:0 28px 28px;">
                <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="border-collapse:collapse; border-top:1px solid {COSMOZPAY_BORDER};">
                  <tr>
                    <td style="padding-top:18px;">
                      <p style="margin:0; font-size:12px; line-height:20px; color:{COSMOZPAY_MUTED};">{safe_footer}</p>
                      {support_html}
                    </td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:0 28px 28px; text-align:center; font-size:12px; color:{COSMOZPAY_MUTED};">
                © 2026 CosmozPay. All rights reserved.
              </td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>
"""

    plain_lines = [safe_subject, "", safe_title, ""]
    if safe_greeting:
        plain_lines.append(safe_greeting)
        plain_lines.append("")
    plain_lines.extend(safe_lines)
    if safe_cta_url:
        plain_lines.append("")
        plain_lines.append(f"{safe_cta_text}: {safe_cta_url}")
    if safe_footer:
        plain_lines.append("")
        plain_lines.append(safe_footer)
    if safe_support:
        plain_lines.append(f"Support: {safe_support}")
    plain_text = "\n".join(plain_lines).strip() + "\n"

    return html_document, plain_text
