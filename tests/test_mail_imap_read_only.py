"""Paket Tracker AT: main mailbox read-only (EXAMINE, server-side search, no flags)."""

from datetime import date

from custom_components.parcel_tracker.mail.imap import MailboxClient
from custom_components.parcel_tracker.mail.readonly import (
    GMAIL_CAPABILITY,
    generic_criteria,
    gmail_query,
    search_args,
)

from .test_mail_imap import RAW, FakeImap


class ReadOnlyImap(FakeImap):
    """FakeImap that takes EXAMINE (select readonly=True) and answers UIDVALIDITY."""

    uidvalidity = b"1"

    def select(self, mailbox, readonly=False):
        self.calls.append(("select", mailbox, readonly))
        return self._status("SELECT"), [b"2"]

    def response(self, code):
        return code, [type(self).uidvalidity]


def _client(**fake_kwargs):
    FakeImap.instances = []

    def factory(host, port, ssl_context=None, timeout=None):
        return ReadOnlyImap(host, port, ssl_context, timeout, **fake_kwargs)

    return MailboxClient(
        "imap.gmail.com", "florian@example.org", "secret", factory=factory, read_only=True
    )


def _commands(fake):
    return [c[1] for c in fake.calls if c[0] == "uid"]


def test_gmail_query_lists_senders_and_subjects():
    query = gmail_query(3)
    assert query.startswith("newer_than:3d -in:sent (")
    assert "from:post.at" in query
    assert "subject:zugestellt" in query


def test_generic_criteria_is_since_plus_a_valid_or_tree():
    args = generic_criteria(date(2026, 3, 5), 3)
    assert args[:2] == ["SINCE", "02-Mar-2026"]
    tree = args[2]
    assert tree.startswith("(OR FROM ") and tree.endswith(")")
    # n keys need n-1 ORs
    keys = tree.count("FROM ") + tree.count("SUBJECT ")
    assert tree.count("OR ") == keys - 1


def test_search_args_uses_gmail_raw_only_with_the_capability():
    today = date(2026, 11, 24)
    gmail = search_args({"IMAP4REV1", GMAIL_CAPABILITY}, today)
    assert gmail[0] == "X-GM-RAW" and gmail[1].startswith('"newer_than:')
    assert search_args({"IMAP4REV1"}, today)[0] == "SINCE"


def test_read_only_examines_and_peeks_without_flags():
    client = _client(mails={"7": RAW, "9": RAW}, caps=("IMAP4REV1", GMAIL_CAPABILITY))
    assert client.fetch_unseen() == [("7", RAW), ("9", RAW)]
    fake = FakeImap.instances[0]
    assert ("select", "INBOX", True) in fake.calls
    search = next(c for c in fake.calls if c[:2] == ("uid", "SEARCH"))
    assert search[3] == "X-GM-RAW"
    assert "UNSEEN" not in search
    fetches = [c for c in fake.calls if c[:2] == ("uid", "FETCH")]
    assert all(c[3] == "(BODY.PEEK[])" for c in fetches)
    assert "STORE" not in _commands(fake)


def test_read_only_falls_back_to_since_or_without_gmail():
    client = _client(mails={"3": RAW})
    client.fetch_unseen()
    search = next(c for c in FakeImap.instances[0].calls if c[:2] == ("uid", "SEARCH"))
    assert search[3] == "SINCE"


def test_finish_touches_nothing_and_mails_are_not_read_twice():
    client = _client(mails={"7": RAW, "9": RAW})
    mails = client.fetch_unseen()
    client.finish([(uid, "Paket-Tracker-Verarbeitet") for uid, _ in mails], move=True)
    # finish opened no connection at all: still only the fetch connection
    assert len(FakeImap.instances) == 1
    assert client.fetch_unseen() == []
    second = FakeImap.instances[-1]
    assert not [c for c in second.calls if c[:2] == ("uid", "FETCH")]
    for fake in FakeImap.instances:
        assert not {"STORE", "COPY", "MOVE"} & set(_commands(fake))
        assert ("expunge",) not in fake.calls
        assert not [c for c in fake.calls if c[0] == "create"]


def test_new_uidvalidity_forgets_what_was_looked_at():
    client = _client(mails={"7": RAW})
    client.finish([(uid, None) for uid, _ in client.fetch_unseen()], move=False)
    ReadOnlyImap.uidvalidity = b"2"
    try:
        assert client.fetch_unseen() == [("7", RAW)]
    finally:
        ReadOnlyImap.uidvalidity = b"1"


def test_purge_does_nothing_in_read_only_mode():
    client = _client(mails={"7": RAW})
    assert client.purge_processed(30, date(2026, 11, 24)) == 0
    assert FakeImap.instances == []


def _search_days(fake):
    search = next(c for c in fake.calls if c[:2] == ("uid", "SEARCH"))
    return search[4]


def test_first_search_takes_stock_of_two_weeks_then_three_days():
    client = _client(mails={"7": RAW}, caps=("IMAP4REV1", GMAIL_CAPABILITY))
    client.fetch_unseen()
    assert "newer_than:14d" in _search_days(FakeImap.instances[0])
    client.fetch_unseen()
    assert "newer_than:3d" in _search_days(FakeImap.instances[1])


def test_stock_taking_continues_while_over_the_fetch_limit(monkeypatch):
    monkeypatch.setattr("custom_components.parcel_tracker.mail.imap.FETCH_LIMIT", 1)
    client = _client(mails={"7": RAW, "9": RAW}, caps=("IMAP4REV1", GMAIL_CAPABILITY))
    first = client.fetch_unseen()
    client.finish([(uid, None) for uid, _ in first], move=False)
    second = client.fetch_unseen()
    assert "newer_than:14d" in _search_days(FakeImap.instances[1])
    assert [u for u, _ in first + second] == ["7", "9"]
    client.finish([(uid, None) for uid, _ in second], move=False)
    client.fetch_unseen()
    assert "newer_than:3d" in _search_days(FakeImap.instances[2])


def test_search_covers_the_parsers_senders_but_not_amazon_marketing():
    from custom_components.parcel_tracker.mail.amazon import AMAZON_SENDERS, RETURNS_SENDER
    from custom_components.parcel_tracker.mail.at_shops import AT_SHOPS
    from custom_components.parcel_tracker.mail.readonly import SENDER_WORDS

    for sender in (*AMAZON_SENDERS, RETURNS_SENDER):
        assert any(word in sender for word in SENDER_WORDS), sender
    for domain in AT_SHOPS:
        assert domain in SENDER_WORDS
    assert not any(word in "prime@amazon.de" for word in SENDER_WORDS)
    assert not any(word in "customer-reviews-messages@amazon.de" for word in SENDER_WORDS)
