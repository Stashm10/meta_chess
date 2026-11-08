from backend.audio.speaker import Speaker


class FakeSay:
    def __init__(self, device_works: bool = True):
        self.device_works = device_works
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str]) -> bool:
        self.calls.append(cmd)
        return self.device_works or "-a" not in cmd

    @property
    def texts(self) -> list[str]:
        return [c[-1] for c in self.calls]


def make(device_works=True, **kw):
    fake = FakeSay(device_works)
    return Speaker(device="BlackHole 2ch", voice="", rate=190, runner=fake, **kw), fake


def test_speaks_in_order_through_device():
    sp, fake = make()
    sp.say("Board locked, you are White")
    sp.say("Pawn to e4")
    sp.wait()
    assert fake.texts == ["Board locked, you are White", "Pawn to e4"]
    assert all(c[:5] == ["say", "-r", "190", "-a", "BlackHole 2ch"] for c in fake.calls)
    sp.stop()


def test_dedupes_repeated_text_but_repeat_forces():
    sp, fake = make()
    assert sp.say("Knight to f3")
    assert not sp.say("Knight to f3")
    assert sp.repeat()
    sp.wait()
    assert fake.texts == ["Knight to f3", "Knight to f3"]
    sp.stop()


def test_falls_back_to_default_output_once():
    sp, fake = make(device_works=False)
    sp.say("one")
    sp.say("two")
    sp.wait()
    # First phrase: device attempt fails, retried without -a. Second: straight to default.
    assert [("-a" in c, c[-1]) for c in fake.calls] == [(True, "one"), (False, "one"), (False, "two")]
    assert sp.device_ok is False
    sp.set_device("Other")
    assert sp.device_ok is None
    sp.stop()


def test_repeat_with_nothing_said():
    sp, _ = make()
    assert not sp.repeat()
