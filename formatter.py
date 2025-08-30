import html
from datetime import date
from pathlib import Path


def build_html_email(schedule: str, items: list[dict], newsletter_title: str, intro_text: str, image_url: str) -> str:

    schedule_naam = 'dagelijkse' if schedule == 'daily' else 'weekelijkse'
    footer_text = f"""Geselecteerd en geschreven door AI • 
    Mogelijk gemaakt door Harmsen AI Consultancy<br>
    Je ontvangt deze mail omdat je bent aangemeld voor de {schedule_naam} nieuwsbrief."""
    switch_url = 'https://harmsen.nl/nieuwsbrief/'
    if schedule == 'daily':
        switch_url += 'wekelijks'
        switch_text = 'wekelijkse'
    else:
        switch_url += 'dagelijks'
        switch_text = 'dagelijkse'

    today = date.today().strftime("%d %b %Y")
    # Kaarten opbouwen
    cards_html = []
    for item in items:
        title = html.escape(item.get("title", ""))
        summary = html.escape(item.get("summary", ""))
        summary = '<p>' + summary.replace('\n\n', '</p><p>').replace('\n', '<br>') + '</p>'
        links = item.get("links") or []
        # Linklijst
        links_html = ""
        if links:
            link_tags = []
            for i, url in enumerate(links, start=1):
                safe_url = html.escape(url).split("?utm")[0].split("&utm")[0]
                try:
                    domain = safe_url.split("://")[1].split("/")[0].split(".")[-2]
                except:
                    domain = "bron"
                link_tags.append(f'<a href="{safe_url}" target="_blank" style="color:#0b5cab;text-decoration:underline;">{domain}</a>')
            links_html = " · ".join(link_tags)

        cards_html.append(f"""
            <!-- Card -->
            <tr>
                <td style="padding:16px 20px;border:1px solid #e6e6e6;border-radius:8px;background:#ffffff;">
                    <h3 style="margin:0 0 8px 0;font-family:Inter,Segoe UI,Arial,sans-serif;font-size:18px;line-height:1.3;color:#111111;">{title}</h3>
                    <p style="margin:0 0 10px 0;font-family:Inter,Segoe UI,Arial,sans-serif;font-size:14px;line-height:1.6;color:#333333;">{summary}</p>
                    <p style="margin:0;font-family:Inter,Segoe UI,Arial,sans-serif;font-size:14px;">
                        {links_html}
                    </p>
                </td>
            </tr>
            <tr><td style="height:14px;line-height:14px;font-size:0;">&nbsp;</td></tr>
        """)

    cards = "\n".join(cards_html)

    html_doc = f"""<!doctype html>
<html lang="nl">
<head>
    <meta charset="utf-8">
    <meta name="x-apple-disable-message-reformatting">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{html.escape(newsletter_title)} — {today}</title>
</head>
<body style="margin:0;padding:0;background:#f5f7fb;">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f5f7fb;">
        <tr>
            <td align="center" style="padding:24px 12px;">
                <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:#ffffff;border-radius:12px;border:1px solid #e6ecf3;">
                    <!-- Header -->
                    <tr>
                        <td style="padding:20px 12px 8px 24px;">
                            <table width="100%" cellpadding="0" cellspacing="0" border="0">
                                <tr>
                                    <td style="vertical-align: top;">
                                        <h1 style="margin:0;font-family:Inter,Segoe UI,Arial,sans-serif;font-size:22px;line-height:1.3;color:#0b0c0c;">
                                            {html.escape(newsletter_title)}
                                        </h1>
                                        <p style="margin:8px 0 0 0;font-family:Inter,Segoe UI,Arial,sans-serif;font-size:12px;color:#6b7280;">
                                            {today}
                                        </p>
                                    </td>
                                    <td style="text-align: right; vertical-align: top; padding-right: 0px;">
                                        <img src="https://s3.eu-west-1.amazonaws.com/harmsen.nl/nieuwsbrief/logo_120.png" alt="Harmsen AI Consultancy" style="width:90px; height:90px">
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <!-- Intro -->
                    <tr>
                        <td style="padding:0 24px 16px 24px;">
                            <p style="margin:0;font-family:Inter,Segoe UI,Arial,sans-serif;font-size:14px;line-height:1.6;color:#333333;">
                                {html.escape(intro_text)}
                            </p>
                        </td>
                    </tr>
                    <tr><td style="height:10px;line-height:10px;font-size:0;">&nbsp;</td></tr>
                    <!-- Image -->
                    <tr>
                        <td style="padding:0 24px 16px 24px;">
                            <img src="{image_url}" alt="" style="width:100%; max-width:552px; height:auto; display:block; border-radius:8px;" />
                        </td>
                    </tr>
                    <tr><td style="height:10px;line-height:10px;font-size:0;">&nbsp;</td></tr>
                    <!-- Cards -->
                    <tr>
                        <td style="padding:0 12px 16px 12px;">
                            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                                {cards}
                            </table>
                        </td>
                    </tr>
                    <!-- Footer -->
                    <tr>
                        <td style="padding:14px 24px 20px 24px;border-top:1px solid #e6ecf3;background:#fbfcfe;border-radius:0 0 12px 12px;">
                            <p style="margin:0;font-family:Inter,Segoe UI,Arial,sans-serif;font-size:12px;color:#6b7280;">
                                {footer_text}
                            </p>
                            <p style="margin:8px 0 0 0;font-family:Inter,Segoe UI,Arial,sans-serif;font-size:12px;color:#9ca3af;">
                                <a href="https://harmsen.nl/nieuwsbrief/afmelden/?email=[EMAIL]" style="color:#6b7280;text-decoration:underline;">Afmelden</a> ·
                                <a href="{switch_url}?email=[EMAIL]" style="color:#6b7280;text-decoration:underline;">Wissel naar de {switch_text} nieuwsbrief</a>
                            </p>
                        </td>
                    </tr>
                </table>
                <div style="height:24px;line-height:24px;font-size:0;">&nbsp;</div>
            </td>
        </tr>
    </table>
</body>
</html>"""
    return html_doc


def create_html_email(schedule: str, items: list, title: str, image_url: str):
    html_email = build_html_email(schedule,
        items,
        newsletter_title=title,
        intro_text="Actueel, concreet en to-the-point",
        image_url=image_url
    )
    # Schrijf naar bestand voor test/preview

    cache_file = Path(__file__).parent / "cache" / f"{schedule}_newsletter.html"
    with open(cache_file, "w", encoding="utf-8") as f:
        f.write(html_email)
    print(f"HTML e-mail opgeslagen als {schedule}_newsletter.html")
    return html_email