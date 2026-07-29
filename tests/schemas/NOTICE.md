# Vendored validation schemas

These schemas are test-only copies. Tests load them from disk and never resolve
schema references over the network.

| File | Specification | Authoritative source | SHA-256 | Upstream licensing |
| --- | --- | --- | --- | --- |
| `sarif-schema-2.1.0.json` | OASIS SARIF 2.1.0 OS | <https://docs.oasis-open.org/sarif/sarif/v2.1.0/os/schemas/sarif-schema-2.1.0.json> | `ad6db49878699b091f3eeb765b6e29e92a34bad4da88664d000c923b549c3a25` | OASIS SARIF TC repository license: <https://github.com/oasis-tcs/sarif-spec/blob/main/LICENSE.md> |
| `cff-schema-1.2.0.json` | Citation File Format 1.2.0 | <https://citation-file-format.github.io/1.2.0/schema.json> | `0b8d22140da702d766df318dcff3a91af2f39521298dcf36d76315fd99cc169b` | CFF repository license: <https://github.com/citation-file-format/citation-file-format/blob/main/LICENSE> |

The source URLs are versioned. Updating either file requires updating its
checksum here and re-running the schema-conformance tests. This notice records
upstream licensing references; it does not replace or reinterpret their terms.

The CFF schema is Copyright 2016–2023 the Citation File Format Contributors
and is redistributed unchanged under CC BY 4.0. The SARIF schema is
redistributed unchanged from the OASIS Standard source above under the OASIS
terms linked in the table. Neither upstream project endorses Adduce.
