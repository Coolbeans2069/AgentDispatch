Build and deliver a complete production-grade CSV reporting service that runs fully offline on this machine.

The finished system must let a user drop one or more CSV files into an input folder and get back, for each file:

- a schema summary (column names, inferred types, null counts, distinct counts);
- descriptive statistics for every numeric column (count, mean, median, min, max, standard deviation);
- a short plain-English summary of anything unusual (empty columns, outliers beyond 3 standard deviations, duplicate rows);
- a Markdown report and a machine-readable JSON report with the same content.

Requirements:

1. A command-line entry point: `python -m csvreport <input-folder> <output-folder>`.
2. Standard library only, Python 3.11+.
3. Handles files up to 200 MB without loading them fully into memory.
4. Malformed rows are counted and reported, never silently dropped.
5. A test suite (`python -m unittest`) with fixtures for: an empty file, a file with only headers, mixed types in one column, quoted fields with embedded commas, and a 100,000-row generated file.
6. A README with usage examples and the report format.

Acceptance: every test passes in the sandbox, and running the CLI on the bundled fixtures produces reports whose JSON validates against the documented format.
