# Dataset evidence collected on 2026-10-03

- [Source comparison](source-comparison-20261003.json): aggregate inventory comparison with the existing bronze manifest; records the verification timestamp and scope.
- [Scoped backup comparison](backup-comparison-20261003.md) and [aggregate results](backup-comparison-20261003.json): exploratory comparison, not a replacement source or a pipeline change. Denominators and sampled partitions are recorded in the report.
- [Historical dbt summary](dbt-run-summary-20260926.json): summarizes the existing 2026-09-26 run artifact: 13 successful nodes, 211 passing tests and 9 warning tests. The original artifact does not record a source Git revision. This run was not repeated for publication.

These artifacts support the findings in `docs/known_issues.md` and the corrected H7 interpretation in `docs/data_findings.md`. Full source-data queries were not rerun when preparing this documentation PR. The proposed requirement responses remain recommendations, not implemented runtime behavior.

The source-example workbook and its inspection dump are excluded because they contain individual source records. This directory publishes aggregate evidence only.
