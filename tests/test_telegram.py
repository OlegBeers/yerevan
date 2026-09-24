from datetime import datetime, timezone

import requests

from taps.state import empty_state
from taps.telegram import API, Alerter, SendOutcome, send_message

NOW = datetime(2026, 9, 24, 13, 0, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakePost:
    """Returns (or raises) the queued items one per call and records every call."""

    def __init__(self, *items):
        self.items = list(items)
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeSleep:
    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


OK = FakeResponse(200, {"ok": True, "result": {"message_id": 7}})


def too_many(retry_after=3):
    return FakeResponse(429, {"ok": False, "error_code": 429, "description": "Too Many Requests: retry after 3",
                              "parameters": {"retry_after": retry_after}})


def send(post, sleep=None, **kwargs):
    return send_message("TOKEN", "-100123", "<b>Новое</b>", post=post, sleep=sleep or FakeSleep(), **kwargs)


def test_payload_is_silent_html_without_preview_and_uses_timeout():
    post = FakePost(OK)
    send(post)
    [(url, kwargs)] = post.calls
    assert url == API.format(token="TOKEN") == "https://api.telegram.org/botTOKEN/sendMessage"
    assert kwargs["timeout"] == 30
    assert kwargs["json"] == {
        "chat_id": "-100123",
        "text": "<b>Новое</b>",
        "parse_mode": "HTML",
        "disable_notification": True,
        "link_preview_options": {"is_disabled": True},
    }


def test_payload_has_url_button_and_custom_timeout_and_loud_mode():
    post = FakePost(OK)
    send(post, button=("Открыть список", "https://example.github.io/yerevan-taps/"), silent=False, timeout=5)
    payload = post.calls[0][1]["json"]
    assert payload["reply_markup"] == {
        "inline_keyboard": [[{"text": "Открыть список", "url": "https://example.github.io/yerevan-taps/"}]]}
    assert payload["disable_notification"] is False
    assert post.calls[0][1]["timeout"] == 5


def test_ok_is_sent():
    assert send(FakePost(OK)) == SendOutcome("sent")


def test_429_sleeps_retry_after_and_retries_once():
    post, sleep = FakePost(too_many(3), OK), FakeSleep()
    assert send(post, sleep).status == "sent"
    assert sleep.calls == [3]
    assert len(post.calls) == 2
    assert post.calls[0][1]["json"] == post.calls[1][1]["json"]


def test_429_twice_is_rejected():
    post, sleep = FakePost(too_many(3), too_many(3)), FakeSleep()
    outcome = send(post, sleep)
    assert outcome.status == "rejected"
    assert "Too Many Requests" in outcome.description
    assert len(post.calls) == 2
    assert sleep.calls == [3]


def test_429_huge_or_missing_retry_after_is_bounded():
    sleep = FakeSleep()
    send(FakePost(too_many(86400), OK), sleep)
    send(FakePost(FakeResponse(429, {"ok": False, "error_code": 429, "description": "x"}), OK), sleep)
    assert sleep.calls == [60, 1]


def test_400_chat_not_found_is_rejected_with_description():
    post = FakePost(FakeResponse(400, {"ok": False, "error_code": 400, "description": "Bad Request: chat not found"}))
    outcome = send(post)
    assert outcome == SendOutcome("rejected", "Bad Request: chat not found", None)
    assert len(post.calls) == 1


def test_403_is_rejected():
    post = FakePost(FakeResponse(403, {"ok": False, "error_code": 403,
                                       "description": "Forbidden: bot was kicked from the supergroup chat"}))
    assert send(post).status == "rejected"


def test_migrate_to_chat_id_is_rejected_and_carries_new_id():
    body = {"ok": False, "error_code": 400, "description": "Bad Request: group chat was upgraded to a supergroup chat",
            "parameters": {"migrate_to_chat_id": -1001234567890}}
    outcome = send(FakePost(FakeResponse(400, body)))
    assert outcome.status == "rejected"
    assert outcome.migrate_to_chat_id == -1001234567890
    assert "upgraded" in outcome.description


def test_timeout_is_unknown():
    outcome = send(FakePost(requests.Timeout("read timed out")))
    assert outcome.status == "unknown"
    assert "Timeout" in outcome.description


def test_exception_text_with_the_bot_token_never_reaches_the_outcome():
    leaky = requests.ConnectionError("HTTPSConnectionPool: Max retries with url: /bot123456:SECRET/sendMessage")
    outcome = send(FakePost(leaky))
    assert outcome.status == "unknown"
    assert "SECRET" not in outcome.description and "123456" not in outcome.description
    assert outcome.description == "ConnectionError"


def test_connection_error_is_unknown():
    assert send(FakePost(requests.ConnectionError("reset"))).status == "unknown"


def test_any_request_exception_is_unknown():
    assert send(FakePost(requests.exceptions.SSLError("bad cert"))).status == "unknown"


def test_502_is_unknown_even_with_json_body():
    post = FakePost(FakeResponse(502, {"ok": False, "error_code": 502, "description": "Bad Gateway"}))
    outcome = send(post)
    assert outcome.status == "unknown"
    assert "502" in outcome.description
    assert len(post.calls) == 1


def test_non_json_body_is_unknown():
    outcome = send(FakePost(FakeResponse(200, ValueError("Expecting value"))))
    assert outcome.status == "unknown"


def test_json_without_ok_flag_is_unknown():
    assert send(FakePost(FakeResponse(200, ["ok"]))).status == "unknown"
    assert send(FakePost(FakeResponse(200, {"result": {}}))).status == "unknown"


def test_timeout_on_retry_after_429_is_unknown():
    assert send(FakePost(too_many(1), requests.Timeout())).status == "unknown"


# ---- Alerter ----

class FakeSend:
    def __init__(self, status="sent"):
        self.status = status
        self.texts = []

    def __call__(self, text):
        self.texts.append(text)
        return SendOutcome(self.status)


def test_same_key_and_text_twice_is_queued_once_across_runs():
    state = empty_state(NOW)
    first = Alerter(state)
    first.alert("source:beer-city", "Beer City: не удалось проверить")
    first.alert("source:beer-city", "Beer City: не удалось проверить")
    assert first.pending_text().count("Beer City") == 1
    assert len(state.alerts["source:beer-city"]) == 12

    second = Alerter(state)  # next run, same state
    second.alert("source:beer-city", "Beer City: не удалось проверить")
    assert second.pending_text() is None


def test_changed_text_is_queued_again():
    state = empty_state(NOW)
    Alerter(state).alert("corrections", "ошибка YAML в строке 3")
    alerter = Alerter(state)
    alerter.alert("corrections", "ошибка YAML в строке 5")
    assert "строке 5" in alerter.pending_text()


def test_resolve_then_same_text_is_queued_again():
    state = empty_state(NOW)
    Alerter(state).alert("source:parma", "Parma: не удалось проверить")
    alerter = Alerter(state)
    alerter.resolve("source:parma")
    assert "source:parma" not in state.alerts
    alerter.resolve("source:never-alerted")  # no error
    alerter.alert("source:parma", "Parma: не удалось проверить")
    assert "Parma" in alerter.pending_text()


def test_resolve_drops_alert_queued_in_same_run():
    alerter = Alerter(empty_state(NOW))
    alerter.alert("source:parma", "Parma: не удалось проверить")
    alerter.resolve("source:parma")
    assert alerter.pending_text() is None


def test_pending_text_combines_alerts_into_one_escaped_message():
    alerter = Alerter(empty_state(NOW))
    alerter.alert("a", "Beer City: не удалось проверить")
    alerter.alert("b", "сводка, возможно, не дошла: <Timeout> & обрыв")
    text = alerter.pending_text()
    assert text.startswith("⚠️")
    assert text == ("⚠️ taps:\n"
                    "• Beer City: не удалось проверить\n"
                    "• сводка, возможно, не дошла: &lt;Timeout&gt; &amp; обрыв")


def test_pending_text_stays_within_telegram_limit():
    alerter = Alerter(empty_state(NOW))
    for i in range(60):
        alerter.alert(f"k{i}", f"{i}: " + "ошибка " * 20)
    text = alerter.pending_text()
    assert len(text) <= 4096
    assert text.endswith("предупреждений не поместилось")
    assert "• 0: " in text


def test_flush_sends_once_and_clears_queue():
    alerter = Alerter(empty_state(NOW))
    alerter.alert("a", "первое")
    alerter.alert("b", "второе")
    fake = FakeSend()
    alerter.flush(fake)
    assert fake.texts == ["⚠️ taps:\n• первое\n• второе"]
    assert alerter.pending_text() is None
    alerter.flush(fake)
    assert len(fake.texts) == 1


def test_flush_with_nothing_queued_does_not_send():
    fake = FakeSend()
    assert Alerter(empty_state(NOW)).flush(fake) is None
    assert fake.texts == []


def test_flush_returns_the_send_outcome():
    for status in ("sent", "rejected", "unknown"):
        alerter = Alerter(empty_state(NOW))
        alerter.alert("a", "первое")
        assert alerter.flush(FakeSend(status)).status == status


def test_rejected_flush_forgets_hashes_so_next_run_retries():
    state = empty_state(NOW)
    state.alerts["old"] = "abc"
    alerter = Alerter(state)
    alerter.alert("a", "первое")
    alerter.flush(FakeSend("rejected"))
    assert state.alerts == {"old": "abc"}
    retry = Alerter(state)
    retry.alert("a", "первое")
    assert retry.pending_text() is not None


def test_alert_dedupe_by_key_ignores_changed_text_until_resolved():
    state = empty_state(NOW)
    first = Alerter(state)
    first.alert("source:parma", "parma: не удалось получить данные (network)", dedupe_by_key=True)
    assert first.pending_text() is not None

    second = Alerter(state)   # next run, same state, same key, different text
    second.alert("source:parma", "parma: не удалось получить данные (http)", dedupe_by_key=True)
    assert second.pending_text() is None

    second.resolve("source:parma")
    third = Alerter(state)
    third.alert("source:parma", "parma: не удалось получить данные (network)", dedupe_by_key=True)
    assert third.pending_text() is not None


def test_unknown_flush_keeps_hashes():
    state = empty_state(NOW)
    alerter = Alerter(state)
    alerter.alert("a", "первое")
    alerter.flush(FakeSend("unknown"))
    assert "a" in state.alerts
