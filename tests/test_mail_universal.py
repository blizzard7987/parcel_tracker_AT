"""Paket Tracker AT: shipping mails of any shop - carriers' tracking links, wider search."""

from email.message import EmailMessage

from custom_components.parcel_tracker.mail import parse_mail
from custom_components.parcel_tracker.mail.base import link_numbers
from custom_components.parcel_tracker.mail.readonly import gmail_query


def _mail(html: str, text: str = "Ihre Bestellung wurde versendet.") -> EmailMessage:
    m = EmailMessage()
    m["From"] = "Shop <service@kleiner-shop.example>"
    m["To"] = "max@example.com"
    m["Subject"] = "Ihre Bestellung ist auf dem Weg"
    m["Date"] = "Tue, 24 Nov 2026 10:00:00 +0000"
    m.set_content(text)
    m.add_alternative(html, subtype="html")
    return m


def test_tracking_links_of_carriers():
    assert link_numbers([
        "https://www.dhl.de/de/privatkunden/pakete-empfangen/verfolgen.html?piececode=00340434161094042557",
        "https://tracking.dpd.de/status/de_DE/parcel/06215310792328",
        "https://www.mydpd.at/meine-pakete?parcelNumber=06215310792329",
        "https://gls-group.eu/AT/de/paket-verfolgen?match=312176143541",
        "https://www.ups.com/track?loc=de_AT&tracknum=1Z999AA10123456784",
        "https://www.myhermes.at/sendungsverfolgung/trackingnummer/12345678901234/",
        "https://www.dhlparcel.nl/nl/consument/volg-je-pakket?tt=JVGL06009999000499999999",
        "https://www.example.com/track?parcelno=12345678901234",
    ]) == [
        ("dhl", "00340434161094042557"),
        ("dpd", "06215310792328"),
        ("dpd", "06215310792329"),
        ("gls", "31217614354"),
        ("ups", "1Z999AA10123456784"),
        ("hermes", "12345678901234"),
        ("dhl", "JVGL06009999000499999999"),
    ]


def test_any_shop_with_a_tracking_button_makes_a_parcel():
    html = ('<p>Dein Paket ist unterwegs.</p><a href="https://tracking.dpd.de/status/de_DE/'
            'parcel/06215310792328">Sendung verfolgen</a>')
    [u] = parse_mail(_mail(html)).updates
    assert (u.number, u.carrier) == ("06215310792328", "dpd")


def test_dhl_ecommerce_number_anywhere():
    [u] = parse_mail(_mail("<p>x</p>", "Sendungsverfolgung: JVGL06009999000499999999")).updates
    assert (u.number, u.carrier) == ("JVGL06009999000499999999", "dhl")


def test_gmail_search_reads_the_text_of_any_sender():
    q = gmail_query()
    assert '"Sendungsnummer"' in q and '"Sendung verfolgen"' in q and '"tracking number"' in q
