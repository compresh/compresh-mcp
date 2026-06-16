# Third-Party Notices

This package, `compresh-mcp` (BUSL-1.1), bundles third-party code.

---

## tulbase (MIT)

The `compresh_mcp.tulbase` subpackage is a vendored copy of
[tulbase](https://github.com/compresh/tulbase), a depth-aware context
compression library for LLM proxies.

- **License**: MIT
- **Copyright**: © 2026 Compresh Ltd
- **Upstream**: https://github.com/compresh/tulbase
- **Vendored version**: 0.2.0
- **License text**: see `src/compresh_mcp/tulbase/LICENSE`

The vendored copy is the canonical reference implementation. The standalone
`tulbase` PyPI package (when published) will contain identical code. Vendoring
exists to keep `compresh-mcp` self-contained on PyPI without forcing a
separate dependency.

If you only need the MIT open-core compression layer (turn-box, protection zone,
compose, retrieval) without the Compresh paid-tier TUL 2.0 layer, prefer
installing the standalone package once it ships:

```bash
pip install tulbase
```

---

## Acknowledgements

- [Model Context Protocol](https://modelcontextprotocol.io/) — Anthropic
- [sentence-transformers](https://www.sbert.net/) — UKPLab (MIT)
- [DuckDB](https://duckdb.org/) — DuckDB Foundation (MIT)
