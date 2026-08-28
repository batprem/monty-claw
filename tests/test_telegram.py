from monty_claw.channels.telegram import TelegramChannel, chunk_text


def test_chunk_short() -> None:
    assert chunk_text('hello') == ['hello']
    assert chunk_text('') == []


def test_chunk_long_prefers_newlines() -> None:
    text = ('line one\n' * 1000).strip()  # ~9000 chars
    chunks = chunk_text(text)
    assert all(len(c) <= 4096 for c in chunks)
    assert ''.join(c + '\n' for c in chunks).strip() == text


def test_chunk_no_newline() -> None:
    text = 'x' * 10000
    chunks = chunk_text(text)
    assert [len(c) for c in chunks] == [4096, 4096, 1808]
    assert ''.join(chunks) == text


def test_parse_update_text_message() -> None:
    channel = TelegramChannel('token')
    inbound = channel.parse_update({
        'update_id': 7,
        'message': {'text': 'hi', 'chat': {'id': 42}},
    })
    assert inbound is not None
    assert (inbound.chat_id, inbound.text, inbound.update_id) == ('42', 'hi', 7)


def test_parse_update_ignores_non_text() -> None:
    channel = TelegramChannel('token')
    assert channel.parse_update({'update_id': 8, 'message': {'photo': [], 'chat': {'id': 1}}}) is None
    assert channel.parse_update({'update_id': 9, 'channel_post': {'text': 'x'}}) is None
