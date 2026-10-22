"""Evidence timing used only by the central EventManager."""
class Evidence:
    def __init__(self, gap=.5):
        self.gap = gap
        self.reset()

    def reset(self):
        self.total = 0.
        self.last = None
        self.previous = False
        self.count = 0
        self.unknown_since = None

    def update(self, value, now, hold):
        if self.last is not None and now-self.last > self.gap:
            self.reset()
        if value is None:
            if self.unknown_since is None:
                self.unknown_since = now
            elif now-self.unknown_since > self.gap:
                self.reset()
                self.unknown_since = now
        elif self.unknown_since is not None:
            if now-self.unknown_since > self.gap:
                self.reset()
            self.unknown_since = None
        if value is False:
            self.reset()
        elif value is True:
            if self.previous and self.last is not None:
                self.total += max(0., now-self.last)
            self.count += 1
        self.previous = value is True
        self.last = now
        return self.count >= 3 and self.total+1e-9 >= hold

