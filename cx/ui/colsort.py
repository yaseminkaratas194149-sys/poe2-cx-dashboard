"""ColumnSort — header-click sort state, shared by the currency cards (two
ttk.Treeviews in cx_panel) and the uniques browser (a RowTable).

One rule on both tabs: the first click on a column header sorts the list by that
column high → low; the second click on the same header flips it low → high, and
so on. The name column (#0) is text, so it opens A → Z instead. The state is just
(column, descending): the panels own the sort keys and the row moves, this only
decides the direction, orders a sequence, and paints the arrow into the active
header's title.
"""

ARROW = {True: " ▾", False: " ▴"}       # header marker: descending / ascending


class ColumnSort:
    def __init__(self, text_cols=("#0",)):
        self.col = None          # active column id; None = the view's own order
        self.desc = True
        self._text = frozenset(text_cols)

    def click(self, col):
        """Header click on *col*: the same column flips the direction; a new one
        opens high → low (a number column) or A → Z (a text column)."""
        if col == self.col:
            self.desc = not self.desc
        else:
            self.col, self.desc = col, col not in self._text

    def reset(self):
        """Back to the view's own order (no arrow)."""
        self.col, self.desc = None, True

    @property
    def mark(self):
        """The subtitle's direction glyph: ↓ high → low, ↑ low → high."""
        return "↓" if self.desc else "↑"

    def title(self, col, base):
        """*base* header text, plus the direction arrow when *col* is active."""
        return base + ARROW[self.desc] if (base and col == self.col) else base

    def order(self, seq, key):
        """*seq* reordered by *key* in the active direction. Items whose key is
        None (no value in that column) trail in either direction; ties keep their
        incoming order (the sort is stable), so the view's default order shows
        through between equal values."""
        keyed = [(key(x), x) for x in seq]
        have = [kx for kx in keyed if kx[0] is not None]
        have.sort(key=lambda kx: kx[0], reverse=self.desc)
        return [x for _, x in have] + [x for k, x in keyed if k is None]
