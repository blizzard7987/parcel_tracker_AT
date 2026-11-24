"""Paket Tracker AT: read the main mailbox without changing anything in it.

The original reads a mailbox of its own (fed by a forwarding rule) and marks or files
every mail it read. In read-only mode the folder is opened with EXAMINE, no flag is
set, nothing is moved or deleted. Which mails are read comes from a server-side search:
on Gmail the Gmail search syntax (X-GM-RAW), elsewhere SINCE plus an OR of sender and
subject words. Mails already looked at in this session are skipped by UID; mails that
were applied are skipped after a restart too (Message-ID, as in the original).
"""

from __future__ import annotations

from datetime import date, timedelta

# How far back the search looks. The original ignores mails older than 14 days anyway;
# a short window keeps the fetch small on a busy mailbox.
READ_ONLY_DAYS = 3
# First search after a start (and after a new UIDVALIDITY): a stock-taking of everything
# still on its way. Matches MAIL_MAX_AGE, older shipping mails are ignored anyway.
INITIAL_DAYS = 14

# Sender words: carriers, marketplaces and Austrian senders with shipping mails.
SENDER_WORDS = (
    # Amazon only with the senders the parser reads (not Prime, reviews, Spar-Abo, ...)
    "bestellbestaetigung@amazon",
    "versandbestaetigung@amazon",
    "shipment-tracking@amazon",
    "order-update@amazon",
    "pickup-point@amazon",
    "rueckgabe@amazon",
    "dhl",
    "dpd",
    "gls",
    "hermes",
    "ups.com",
    "post.at",
    "ebay",
    "aliexpress",
    "eventim",
    "shopify",
    "aftership",
    "parcellab",
    # Austrian shops (mail/at_shops.py)
    "dm.at",
    "shop-apotheke.at",
    "apotheke.at",
    "aboutyou.at",
    "asambeauty.com",
    "pharmeo.at",
    "apol.at",
    "exporto.at",
)

# Subject words of shipping mails (ASCII only: the search must not need a charset).
SUBJECT_WORDS = (
    "versand",
    "versendet",
    "versandt",
    "verschickt",
    "unterwegs",
    "sendung",
    "paket",
    "lieferung",
    "zugestellt",
    "zustellung",
    "tracking",
    "shipped",
    "abholbereit",
    "abholung",
)

GMAIL_CAPABILITY = "X-GM-EXT-1"


def gmail_query(days: int = READ_ONLY_DAYS) -> str:
    """The Gmail search for shipping mails of the last ``days`` days (not sent ones)."""
    senders = " OR ".join(f"from:{w}" for w in SENDER_WORDS)
    subjects = " OR ".join(f"subject:{w}" for w in SUBJECT_WORDS)
    return f"newer_than:{days}d -in:sent ({senders} OR {subjects})"


# RFC 3501 month names; strftime("%b") would follow the system locale.
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def _quoted(word: str) -> str:
    return '"' + word.replace("\\", "\\\\").replace('"', '\\"') + '"'


def generic_criteria(today: date, days: int = READ_ONLY_DAYS) -> list[str]:
    """IMAP SEARCH criteria for any server: SINCE plus an OR tree of FROM and SUBJECT.

    IMAP's OR takes exactly two keys, so n keys become n-1 nested ORs in prefix form.
    """
    keys = [f"FROM {_quoted(w)}" for w in SENDER_WORDS]
    keys += [f"SUBJECT {_quoted(w)}" for w in SUBJECT_WORDS]
    tree = keys[-1]
    for key in reversed(keys[:-1]):
        tree = f"OR {key} {tree}"
    since = today - timedelta(days=days)
    return ["SINCE", f"{since.day:02d}-{_MONTHS[since.month - 1]}-{since.year}", f"({tree})"]


def search_args(caps: set[str], today: date, days: int = READ_ONLY_DAYS) -> list[str]:
    """Arguments for ``conn.uid("SEARCH", None, *args)``."""
    if GMAIL_CAPABILITY in caps:
        return ["X-GM-RAW", _quoted(gmail_query(days))]
    return generic_criteria(today, days)
