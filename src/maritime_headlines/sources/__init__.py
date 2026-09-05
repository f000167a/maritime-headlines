"""Source-specific adapters. Network dependencies are imported only when used."""

from . import jmd, kaijipress, splash247, tds

FETCHERS = {"j": jmd.fetch, "k": kaijipress.fetch, "s": splash247.fetch, "t": tds.fetch}
