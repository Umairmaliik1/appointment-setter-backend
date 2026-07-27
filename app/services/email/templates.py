"""Email templates.

Design pattern:
  - One shared shell (`_create_html_wrapper`) with a modern, email-client-safe
    layout (table-based, inline-styled, mobile friendly).
  - Small reusable building-blocks (`_hero`, `_card`, `_field_table`,
    `_meta_footer`, `_link`, `_muted`, `_badge`) that any template composes
    into its body. No inline HTML soup per template.
  - One central palette + font stack so every email looks consistent.
  - Legacy class names (`.details-box`, `.container`, etc.) keep working
    via a small <style> block so existing template bodies render cleanly
    without needing to be refactored.

Add a new template = write a function that builds `content` from the
component helpers and returns (subject, _create_html_wrapper(title, content,
eyebrow=..., badge=..., preheader=...)). Nothing else to invent.
"""

from html import escape as _esc
from typing import Any, Dict, Optional, Sequence, Tuple


class EmailTemplates:
    # ============================================================
    # Design tokens (one place to retune the palette / typography)
    # ============================================================
    INK = "#0F172A"            # primary headline / value text
    INK_2 = "#475569"          # body copy
    INK_3 = "#64748B"          # labels
    MUTED = "#94A3B8"          # placeholders, "(not provided)"
    MUTED_2 = "#CBD5E1"        # tertiary footer text
    ACCENT = "#4F46E5"         # primary accent / links
    ACCENT_INK = "#4338CA"     # text on accent tints
    ACCENT_TINT = "#EEF2FF"    # badge / pill background
    BG = "#F1F5F9"             # page background
    CARD_BG = "#FFFFFF"        # card background
    CARD_BG_2 = "#F8FAFC"      # inner-card / footer background
    BORDER = "#E2E8F0"         # 1px dividers
    FONT = (
        "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,"
        "'Helvetica Neue',Arial,sans-serif"
    )

    # ============================================================
    # Reusable component helpers
    # Every template composes its body from these — no template
    # should ever write a bespoke <table> / <tr> by hand.
    # ============================================================

    @classmethod
    def _muted(cls, text: str) -> str:
        """Italic muted grey — for `(not provided)`, `N/A`, etc."""
        return f'<span style="color:{cls.MUTED}; font-style:italic;">{_esc(text)}</span>'

    @classmethod
    def _link(cls, href: str, text: str) -> str:
        """Accent-coloured link (tel:, mailto:, https://)."""
        return (
            f'<a href="{_esc(href)}" style="color:{cls.ACCENT}; '
            f'text-decoration:none; font-weight:500;">{_esc(text)}</a>'
        )

    @classmethod
    def _badge(cls, text: str) -> str:
        """Pill — for the top-right vertical tag."""
        if not text:
            return ""
        return (
            f'<span style="display:inline-block; padding:4px 10px; '
            f'background-color:{cls.ACCENT_TINT}; color:{cls.ACCENT_INK}; '
            f'font-size:11px; font-weight:600; border-radius:999px; '
            f'letter-spacing:0.04em;">{_esc(text)}</span>'
        )

    @classmethod
    def _hero(cls, headline: str, intro_html: str = "") -> str:
        """The big page headline + optional intro paragraph."""
        bottom_pad = "8px" if intro_html else "24px"
        h1 = (
            f'<h1 style="margin:0 0 12px 0; font-size:22px; line-height:1.35; '
            f'font-weight:700; color:{cls.INK}; letter-spacing:-0.01em;">'
            f'{_esc(headline)}</h1>'
        )
        p = (
            f'<p style="margin:0; font-size:15px; line-height:1.65; '
            f'color:{cls.INK_2};">{intro_html}</p>'
            if intro_html else ""
        )
        return (
            f'<tr><td style="padding:28px 32px {bottom_pad} 32px;">'
            f'{h1}{p}</td></tr>'
        )

    @classmethod
    def _card(cls, label: str, body_html: str) -> str:
        """A grey-tinted content card with an uppercase eyebrow label."""
        return (
            f'<tr><td style="padding:24px 32px 0 32px;">'
            f'<table role="presentation" cellpadding="0" cellspacing="0" '
            f'border="0" width="100%" style="background-color:{cls.CARD_BG_2}; '
            f'border:1px solid {cls.BORDER}; border-radius:12px;">'
            f'<tr><td style="padding:22px 24px;">'
            f'<p style="margin:0 0 14px 0; font-size:11px; font-weight:700; '
            f'color:{cls.MUTED}; text-transform:uppercase; letter-spacing:0.1em;">'
            f'{_esc(label)}</p>'
            f'{body_html}'
            f'</td></tr></table></td></tr>'
        )

    @classmethod
    def _field_table(cls, rows: Sequence[Tuple[str, str]]) -> str:
        """Render label/value rows with thin dividers between them.

        `rows` is a sequence of (label, value_html) tuples. `value_html`
        is interpolated raw — pass pre-rendered helpers (`_link`,
        `_muted`, `_esc(...)`, etc.) so XSS never reaches this method.
        """
        lines = [
            '<table role="presentation" cellpadding="0" cellspacing="0" '
            'border="0" width="100%" style="font-size:15px; line-height:1.5;">'
        ]
        for i, (label, value) in enumerate(rows):
            divider = f"border-top:1px solid {cls.BORDER};" if i > 0 else ""
            lines.append(
                f'<tr>'
                f'<td style="padding:8px 16px 8px 0; color:{cls.INK_3}; '
                f'width:88px; vertical-align:top; font-weight:500; {divider}">'
                f'{_esc(label)}</td>'
                f'<td style="padding:8px 0; vertical-align:top; '
                f'color:{cls.INK}; {divider}">{value}</td>'
                f'</tr>'
            )
        lines.append("</table>")
        return "".join(lines)

    @classmethod
    def _preformatted(cls, text: str) -> str:
        """Multi-line text card body — preserves newlines + spacing."""
        return (
            f'<pre style="margin:0; font-family:{cls.FONT}; font-size:14px; '
            f'line-height:1.7; color:#334155; white-space:pre-wrap; '
            f'word-wrap:break-word;">{_esc(text)}</pre>'
        )

    @classmethod
    def _meta_footer(
        cls,
        primary: Sequence[Tuple[str, str]],
        secondary: Optional[Sequence[Tuple[str, str]]] = None,
    ) -> str:
        """Two-line subtle metadata footer.

        Each list is [(label, value), ...]. Empty values are dropped, so
        callers can pass everything and let unset fields disappear.
        """
        def line(items, value_color):
            parts = []
            for label, value in items:
                if not value:
                    continue
                parts.append(
                    f'{_esc(label)} <strong style="color:{value_color};">'
                    f'{_esc(value)}</strong>'
                )
            return ' &nbsp;·&nbsp; '.join(parts)

        line_1 = line(primary, cls.INK_2)
        line_2 = line(secondary or (), cls.MUTED_2)

        return (
            f'<tr><td style="padding:20px 32px 24px 32px; '
            f'background-color:{cls.CARD_BG_2}; '
            f'border-top:1px solid {cls.BORDER};">'
            f'<p style="margin:0 0 6px 0; font-size:12px; line-height:1.6; '
            f'color:{cls.MUTED};">{line_1}</p>'
            f'<p style="margin:0; font-size:12px; line-height:1.6; '
            f'color:{cls.MUTED_2};">{line_2}</p>'
            f'</td></tr>'
        )

    # ============================================================
    # Generic shell — every email is wrapped in this
    # ============================================================
    @classmethod
    def _create_html_wrapper(
        cls,
        title: str,
        content: str,
        *,
        eyebrow: str = "",
        badge: str = "",
        preheader: str = "",
        # `header_color` is preserved as a kwarg for backwards-compatibility
        # with existing template call sites (appointment_confirmation, etc.).
        # The new shell uses its own design language and ignores it.
        header_color: Optional[str] = None,  # noqa: ARG002
    ) -> str:
        """Modern, email-client-safe shell. All templates funnel through it.

        Args:
            title:     <title> + accessibility name.
            content:   raw <tr>...</tr> rows produced by the component
                       helpers (`_hero`, `_card`, `_meta_footer`, ...).
            eyebrow:   short uppercase label shown top-left
                       (e.g. "New lead", "Confirmation").
            badge:     pill shown top-right (e.g. the vertical name).
            preheader: hidden text shown in the inbox preview line.
        """
        # Top strip: eyebrow + badge. Skipped entirely if both are empty.
        if eyebrow or badge:
            eyebrow_cell = (
                f'<td style="font-size:11px; font-weight:700; '
                f'color:{cls.ACCENT}; text-transform:uppercase; '
                f'letter-spacing:0.12em;">●&nbsp;&nbsp;{_esc(eyebrow)}</td>'
            ) if eyebrow else "<td></td>"
            badge_cell = (
                f'<td align="right">{cls._badge(badge)}</td>' if badge else ""
            )
            top_strip = (
                f'<tr><td style="padding:20px 32px; '
                f'background-color:{cls.CARD_BG}; '
                f'border-bottom:1px solid {cls.CARD_BG_2};">'
                f'<table role="presentation" cellpadding="0" cellspacing="0" '
                f'border="0" width="100%"><tr>{eyebrow_cell}{badge_cell}</tr>'
                f'</table></td></tr>'
            )
        else:
            top_strip = ""

        preheader_html = (
            f'<div style="display:none; max-height:0; overflow:hidden; '
            f'mso-hide:all; visibility:hidden; opacity:0; color:transparent; '
            f'height:0; width:0;">{_esc(preheader)}</div>'
            if preheader else ""
        )

        # Tiny <style> block — backwards-compat for legacy templates that
        # still use class="details-box" etc. New templates use the helpers
        # above and don't depend on this.
        legacy_styles = (
            "<style>"
            f".details-box {{ background-color:{cls.CARD_BG_2}; "
            f"border:1px solid {cls.BORDER}; border-radius:12px; "
            "padding:18px 22px; margin:16px 0; }}"
            f".details-box p {{ margin:6px 0; color:{cls.INK_2}; }}"
            f".details-box a {{ color:{cls.ACCENT}; text-decoration:none; "
            "font-weight:500; }}"
            "</style>"
        )

        return (
            "<!DOCTYPE html>"
            '<html lang="en"><head>'
            '<meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1.0">'
            '<meta name="color-scheme" content="light only">'
            '<meta name="supported-color-schemes" content="light">'
            f'<title>{_esc(title)}</title>'
            f'{legacy_styles}'
            "</head>"
            f'<body style="margin:0; padding:0; background-color:{cls.BG}; '
            f'font-family:{cls.FONT}; color:{cls.INK}; '
            '-webkit-font-smoothing:antialiased; -webkit-text-size-adjust:100%;">'
            f'{preheader_html}'
            f'<table role="presentation" cellpadding="0" cellspacing="0" '
            f'border="0" width="100%" style="background-color:{cls.BG};">'
            '<tr><td align="center" style="padding:32px 16px;">'
            f'<table role="presentation" cellpadding="0" cellspacing="0" '
            f'border="0" width="100%" style="max-width:560px; '
            f'background-color:{cls.CARD_BG}; border-radius:16px; '
            "box-shadow:0 1px 3px rgba(15,23,42,0.06), "
            "0 1px 2px rgba(15,23,42,0.04); overflow:hidden;\">"
            f'{top_strip}'
            f'{content}'
            "</table>"
            f'<p style="margin:20px 0 0 0; font-size:11px; line-height:1.5; '
            f'color:{cls.MUTED}; text-align:center;">'
            "This is an automated message — replies aren't monitored.</p>"
            "</td></tr></table>"
            "</body></html>"
        )

    # ============================================================
    # Templates — composed from the helpers above
    # ============================================================

    @staticmethod
    def generic_submission(email: str, message: str, phone_number: str) -> str:
        """Plain-fields fallback (used by send_raw_email)."""
        return (
            f'<div>'
            f'<div>Email: {_esc(email) or "NOT_PROVIDED"} </div><br>'
            f'<div>Message: {_esc(message) or "NOT_PROVIDED"}</div><br>'
            f'<div>Phone Number: {_esc(phone_number) or "NOT_PROVIDED"}</div>'
            f'</div>'
        )

    @classmethod
    def lead_notification(cls, data: Dict[str, Any]) -> Tuple[str, str]:
        """Universal team-notification email — works for any voice agent.

        Composed entirely from the reusable component helpers above; no
        bespoke HTML. Adding a new vertical means a new prompt — this
        template needs zero changes.
        """
        # ---- normalize inputs ----
        name_raw = str(data.get("customer_name", "")).strip()
        phone_raw = str(data.get("customer_phone", "")).strip()
        email_raw = str(data.get("customer_email", "")).strip()
        summary = str(data.get("summary", "")).strip() or "New lead"
        details_raw = str(data.get("details", "")).strip()
        agent_name = str(data.get("agent_name", "")).strip()
        service_type = str(data.get("service_type", "")).strip()
        tenant_name = str(data.get("tenant_name", "")).strip()
        call_id = str(data.get("call_id", "")).strip()
        captured_at = str(data.get("captured_at", "")).strip()

        # ---- subject (deterministic, inbox-filterable) ----
        prefix = f"[{service_type}] " if service_type else ""
        attribution = f"New lead from {agent_name}" if agent_name else "New lead"
        subject = f"{prefix}{attribution}: {summary}"

        # ---- contact cells ----
        name_cell = (
            f'<span style="font-weight:500; color:{cls.INK};">{_esc(name_raw)}</span>'
            if name_raw else cls._muted("Unknown caller")
        )

        phone_digits = "".join(ch for ch in phone_raw if ch.isdigit() or ch == "+")
        phone_cell = (
            cls._link(f"tel:{phone_digits}", phone_raw or phone_digits)
            if phone_digits else cls._muted("Not provided")
        )

        email_cell = (
            cls._link(f"mailto:{email_raw}", email_raw)
            if email_raw and "@" in email_raw else cls._muted("Not provided")
        )

        # ---- intro paragraph ----
        if agent_name:
            intro = (
                f'A caller just spoke with <strong style="color:{cls.INK};">'
                f'{_esc(agent_name)}</strong> and is ready for follow-up.'
            )
        else:
            intro = "A caller just left their details and is ready for follow-up."

        # ---- compose body from helpers ----
        body = cls._hero(summary, intro) + cls._card(
            "Contact",
            cls._field_table([
                ("Name", name_cell),
                ("Phone", phone_cell),
                ("Email", email_cell),
            ]),
        )
        if details_raw:
            body += cls._card("What they said", cls._preformatted(details_raw))
        body += cls._meta_footer(
            primary=(
                ("Workspace", tenant_name),
                ("Agent", agent_name),
                ("Vertical", service_type),
            ),
            secondary=(
                ("Captured", captured_at),
                ("Call ID", call_id),
            ),
        )

        return subject, cls._create_html_wrapper(
            title="New lead",
            content=body,
            eyebrow="New lead",
            badge=service_type,
            preheader=f"{summary} — ready for callback. Contact details inside.",
        )

    @staticmethod
    def partner_owner_invite(data: Dict[str, Any]) -> Tuple[str, str]:
        subject = f"Welcome to {data.get('platform_name', 'MindRind')} - Set Your Password"
        content = f"""
            <p>Hello {_esc(data.get('owner_name', 'there'))},</p>
            <p>Your partner workspace <strong>{_esc(data.get('partner_name', 'Partner'))}</strong> is ready.</p>
            <p>To activate your account, set your password using the secure link below:</p>
            <div class="details-box">
                <p><a href="{_esc(data.get('setup_password_url', '#'))}" target="_blank" rel="noopener">Set password</a></p>
                <p><strong>Login Email:</strong> {_esc(data.get('owner_email', 'N/A'))}</p>
                <p><strong>Link Expiry:</strong> {data.get('expires_in_hours', 48)} hours</p>
            </div>
            <p>If the button/link doesn't open, copy and paste this URL in your browser:</p>
            <p>{_esc(data.get('setup_password_url', '#'))}</p>
            <p>After setting your password, sign in here:</p>
            <p><a href="{_esc(data.get('login_url', '#'))}" target="_blank" rel="noopener">{_esc(data.get('login_url', '#'))}</a></p>
        """
        body = EmailTemplates._hero("Welcome — set your password", "") + (
            f'<tr><td style="padding:0 32px 28px 32px; color:{EmailTemplates.INK_2}; '
            f'font-size:15px; line-height:1.65;">{content}</td></tr>'
        )
        return subject, EmailTemplates._create_html_wrapper(
            title="Partner Onboarding",
            content=body,
            eyebrow="Onboarding",
            badge="Partner",
        )

    @staticmethod
    def user_setup_invite(data: Dict[str, Any]) -> Tuple[str, str]:
        subject = f"Welcome to {data.get('platform_name', 'MindRind')} - Activate Your Account"
        content = f"""
            <p>Hello {_esc(data.get('recipient_name', 'there'))},</p>
            <p>You were added to <strong>{_esc(data.get('workspace_name', 'your workspace'))}</strong>.</p>
            <p>Set your password to activate your account:</p>
            <div class="details-box">
                <p><a href="{_esc(data.get('setup_password_url', '#'))}" target="_blank" rel="noopener">Set password</a></p>
                <p><strong>Login Email:</strong> {_esc(data.get('recipient_email', 'N/A'))}</p>
                <p><strong>Link Expiry:</strong> {data.get('expires_in_hours', 48)} hours</p>
            </div>
            <p>If needed, paste this URL in your browser:</p>
            <p>{_esc(data.get('setup_password_url', '#'))}</p>
            <p>Login URL: <a href="{_esc(data.get('login_url', '#'))}" target="_blank" rel="noopener">{_esc(data.get('login_url', '#'))}</a></p>
        """
        body = EmailTemplates._hero("Activate your account", "") + (
            f'<tr><td style="padding:0 32px 28px 32px; color:{EmailTemplates.INK_2}; '
            f'font-size:15px; line-height:1.65;">{content}</td></tr>'
        )
        return subject, EmailTemplates._create_html_wrapper(
            title="Account Invitation",
            content=body,
            eyebrow="Invitation",
            badge="Account",
        )

    @staticmethod
    def password_reset(data: Dict[str, Any]) -> Tuple[str, str]:
        subject = f"{data.get('platform_name', 'MindRind')} Password Reset"
        content = f"""
            <p>Hello {_esc(data.get('recipient_name', 'there'))},</p>
            <p>We received a request to reset your password.</p>
            <div class="details-box">
                <p><a href="{_esc(data.get('reset_password_url', '#'))}" target="_blank" rel="noopener">Reset password</a></p>
                <p><strong>Link Expiry:</strong> {data.get('expires_in_minutes', 60)} minutes</p>
            </div>
            <p>If you did not request this, you can ignore this email.</p>
            <p>Direct URL:</p>
            <p>{_esc(data.get('reset_password_url', '#'))}</p>
        """
        body = EmailTemplates._hero("Password reset", "") + (
            f'<tr><td style="padding:0 32px 28px 32px; color:{EmailTemplates.INK_2}; '
            f'font-size:15px; line-height:1.65;">{content}</td></tr>'
        )
        return subject, EmailTemplates._create_html_wrapper(
            title="Password Reset",
            content=body,
            eyebrow="Security",
        )

    @staticmethod
    def appointment_confirmation(data: Dict[str, Any]) -> Tuple[str, str]:
        subject = f"Appointment Confirmed - {data.get('service_type', 'Service')}"
        content = f"""
            <p>Dear {_esc(data.get('customer_name', 'Customer'))},</p>
            <p>Your appointment has been successfully confirmed. Here are the details:</p>
            <div class="details-box">
                <p><strong>Service Type:</strong> {_esc(data.get('service_type', 'N/A'))}</p>
                <p><strong>Date &amp; Time:</strong> {_esc(data.get('appointment_datetime', 'N/A'))}</p>
                <p><strong>Service Address:</strong> {_esc(data.get('service_address', 'N/A'))}</p>
                <p><strong>Appointment ID:</strong> {_esc(data.get('appointment_id', 'N/A'))}</p>
                {f"<p><strong>Service Details:</strong> {_esc(data.get('service_details', ''))}</p>" if data.get('service_details') else ""}
            </div>
            <p>If you need to reschedule or cancel, please contact us at your earliest convenience.</p>
            <p>Thank you for choosing {_esc(data.get('business_name', 'us'))}.</p>
        """
        body = EmailTemplates._hero(
            "Your appointment is confirmed",
            f"We'll see you on <strong style='color:{EmailTemplates.INK};'>{_esc(data.get('appointment_datetime', 'the scheduled date'))}</strong>.",
        ) + (
            f'<tr><td style="padding:0 32px 28px 32px; color:{EmailTemplates.INK_2}; '
            f'font-size:15px; line-height:1.65;">{content}</td></tr>'
        )
        return subject, EmailTemplates._create_html_wrapper(
            title="Appointment Confirmation",
            content=body,
            eyebrow="Confirmation",
            badge=str(data.get("service_type", "")),
        )

    @staticmethod
    def appointment_owner_notification(data: Dict[str, Any]) -> Tuple[str, str]:
        subject = f"New Appointment Booked - {data.get('service_type', 'Service')}"
        content = f"""
            <p>A new appointment has been confirmed. Here are the details:</p>
            <div class="details-box">
                <p><strong>Customer Name:</strong> {_esc(data.get('customer_name', 'N/A'))}</p>
                <p><strong>Customer Email:</strong> {_esc(data.get('customer_email', 'N/A'))}</p>
                <p><strong>Service Type:</strong> {_esc(data.get('service_type', 'N/A'))}</p>
                <p><strong>Date &amp; Time:</strong> {_esc(data.get('appointment_datetime', 'N/A'))}</p>
                <p><strong>Service Address:</strong> {_esc(data.get('service_address', 'N/A'))}</p>
                <p><strong>Appointment ID:</strong> {_esc(data.get('appointment_id', 'N/A'))}</p>
                {f"<p><strong>Service Details:</strong> {_esc(data.get('service_details', ''))}</p>" if data.get('service_details') else ""}
            </div>
        """
        body = EmailTemplates._hero("New appointment booked", "") + (
            f'<tr><td style="padding:0 32px 28px 32px; color:{EmailTemplates.INK_2}; '
            f'font-size:15px; line-height:1.65;">{content}</td></tr>'
        )
        return subject, EmailTemplates._create_html_wrapper(
            title="New Appointment Notification",
            content=body,
            eyebrow="New booking",
            badge=str(data.get("service_type", "")),
        )

    @staticmethod
    def appointment_status_update(data: Dict[str, Any]) -> Tuple[str, str]:
        new_status = data.get("new_status", "updated")
        subject = f"Appointment {new_status.title()} - {data.get('service_type', 'Service')}"
        content = f"""
            <p>Dear {_esc(data.get('customer_name', 'Customer'))},</p>
            <p>Your appointment status has been updated.</p>
            <div class="details-box">
                <p><strong>Previous Status:</strong> {_esc(data.get('old_status', 'N/A'))}</p>
                <p><strong>New Status:</strong> {_esc(new_status)}</p>
                <p><strong>Service Type:</strong> {_esc(data.get('service_type', 'N/A'))}</p>
                <p><strong>Date &amp; Time:</strong> {_esc(data.get('appointment_datetime', 'N/A'))}</p>
                <p><strong>Appointment ID:</strong> {_esc(data.get('appointment_id', 'N/A'))}</p>
                {f"<p><strong>Reason:</strong> {_esc(data.get('cancellation_reason', ''))}</p>" if data.get('cancellation_reason') else ""}
            </div>
            <p>If you have any questions, please contact us.</p>
            <p>Thank you,<br>{_esc(data.get('business_name', 'The Team'))}</p>
        """
        body = EmailTemplates._hero(
            f"Appointment {new_status}", ""
        ) + (
            f'<tr><td style="padding:0 32px 28px 32px; color:{EmailTemplates.INK_2}; '
            f'font-size:15px; line-height:1.65;">{content}</td></tr>'
        )
        return subject, EmailTemplates._create_html_wrapper(
            title="Appointment Status Update",
            content=body,
            eyebrow="Status update",
            badge=str(new_status).title(),
        )

    @staticmethod
    def appointment_reschedule(data: Dict[str, Any]) -> Tuple[str, str]:
        subject = f"Appointment Rescheduled - {data.get('service_type', 'Service')}"
        content = f"""
            <p>Dear {_esc(data.get('customer_name', 'Customer'))},</p>
            <p>Your appointment has been rescheduled.</p>
            <div class="details-box">
                <p><strong>Previous Date &amp; Time:</strong> {_esc(data.get('old_datetime', 'N/A'))}</p>
                <p><strong>New Date &amp; Time:</strong> {_esc(data.get('new_datetime', 'N/A'))}</p>
                <p><strong>Service Type:</strong> {_esc(data.get('service_type', 'N/A'))}</p>
                <p><strong>Service Address:</strong> {_esc(data.get('service_address', 'N/A'))}</p>
                <p><strong>Appointment ID:</strong> {_esc(data.get('appointment_id', 'N/A'))}</p>
                {f"<p><strong>Reason:</strong> {_esc(data.get('reschedule_reason', ''))}</p>" if data.get('reschedule_reason') else ""}
            </div>
            <p>If this new time does not work for you, please contact us as soon as possible.</p>
            <p>Thank you,<br>{_esc(data.get('business_name', 'The Team'))}</p>
        """
        body = EmailTemplates._hero("Appointment rescheduled", "") + (
            f'<tr><td style="padding:0 32px 28px 32px; color:{EmailTemplates.INK_2}; '
            f'font-size:15px; line-height:1.65;">{content}</td></tr>'
        )
        return subject, EmailTemplates._create_html_wrapper(
            title="Appointment Rescheduled",
            content=body,
            eyebrow="Rescheduled",
            badge=str(data.get("service_type", "")),
        )
