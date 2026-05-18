# compresh-mcp Archive

Deprecated source kept for reference. Not included in PyPI wheel.

## 0.1.0-tul1/ — TUL 1.0 layer (moved server-side in 0.2.0)

The `compresh_mcp.tul1` namespace shipped in 0.1.0 contained:
- `QMatrixClassifier`
- `EpistemicClassifier`
- `SemanticStore`
- `Tier1Summarizer` (with Q-protective ranking)

In 0.2.0 these layers were removed from the client package and moved
server-side behind the `/v1/tul1` HTTP endpoint, gated by Compresh API
key + tier check. Reason: pricing matrix says "Free → tulbase only,
Pro → TUL 1.0" but 0.1.0 shipped TUL 1.0 to everyone locally. 0.2.0
realigns code with the documented architecture.

Code preserved here for:
- Git history reference (without git log archaeology)
- Future open-sourcing decision (these classifiers may move to MIT
  tulbase if bench results don't justify paid tier separation)
- Local testing / development

**This directory is NOT shipped in PyPI wheels** (pyproject.toml's
`[tool.setuptools.packages.find]` only includes `src/`).
