# Reproducibility and execution

[Quick start](../README.md#quick-start) | [Protocol](experiments/README.md)

## Environment

The manuscript reports Windows, Python 3.8.10, Gurobi 11.0.3 and an Intel Core i7-12700H workstation with 14 cores, 20 logical processors and 32 GiB RAM. The tested companion environment uses Python 3.8.10, `gurobipy` 11.0.3, NumPy 1.21.5, Matplotlib 3.5.1, pandas 1.2.4 and pytest 8.3.5.

`requirements.txt` lists direct plotting/test dependencies; the model, generators, SAGINFE and validation use the standard library. `requirements-solver.txt` lists the optional Gurobi package. Create a virtual environment and record its resolved versions and hardware with new runs. These dependency lists are not lockfiles for the original experiments.

## Gurobi licensing

Install the Python package using `python -m pip install -r requirements-solver.txt` and configure your own suitable Gurobi license. No license file, token or solver binary is distributed. ILP, LP-RMP, integer RMP and MIP pricing require licensed optimization; pure generation and SAGINFE do not. A restricted license may pass tiny cases but reject formal problem sizes. Parallel workers must also be permitted by the available license and environment.

## Fast validation

From the repository root:

```powershell
python examples/smoke.py
python -m pytest tests -q
$env:RUN_GUROBI_TESTS = '1'
python -m pytest tests_gurobi -q
```

On POSIX shells the last two lines become `RUN_GUROBI_TESTS=1 python -m pytest tests_gurobi -q`. Without the flag and a usable license, solver tests are skipped. A skipped suite is not successful solver validation. The selected suite checks stage identity, topology/request reproducibility, capacities, objective normalization, pricing certification, exact reduced costs, dual signs, finite-pool scope and small serial/parallel cases. Preflight plans follow the formal protocol. Pilot plans vary intrinsic work budgets for diagnostics; v1 templates are separate compatibility fixtures.

`examples/smoke.py` builds an eight-request S20 instance with six slots and integration seed 999, constructs a SAGINFE solution, and checks request and aggregate feasibility. It deliberately differs from the formal $|R|=4|N|$ matrix and reports diagnostic results only.

## Running and plotting

The [protocol](experiments/README.md#6-new-executions-and-outputs) gives commands for ordinary and specialized campaigns. Run from the root to preserve imports and relative configuration paths. Commit changes before formal runs, or deliberately use the root runner's `--allow-dirty` option and retain that provenance.

New outputs belong in ignored `outputs/` or `formal_outputs/` directories. Keep all per-run fields, fingerprints, validity flags, solver statuses and configuration snapshots locally. The portable [plotter](../scripts/plot/plot_new_results.py) accepts explicit new JSONL files and produces plots in ignored `generated/`. It rejects duplicates, invalid solutions and mismatched instance/seed pairings. It does not silently fill absent reference methods or reconstruct specialized historical joins.

The [plotting guide](../scripts/plot/README.md) provides a command for reconstructing all seven supplementary figures from an explicit read-only data directory, plus specialized drawing sources for the paper figures. The numerical inputs are not distributed.

## Reproducibility boundary

The repository supports synthetic instance generation and new algorithm executions. Original per-seed records, frozen numerical tables, solver logs and caches are not distributed, so exact figure reconstruction requires the original data. The [source manifest](source-manifest.json) records SHA-256 hashes of the current repository bytes for its listed files; it is an integrity inventory, not a record of historical experimental inputs. Its coverage is deliberately limited to those paths, and it excludes itself. After editing any listed file, recalculate all listed hashes from the final file bytes and run `python -m pytest tests/test_source_manifest.py -q`. Preserve repository line endings when checking integrity; on Windows, clone with `git -c core.autocrlf=false clone <repository-url>` to avoid checkout-time byte conversion. Git records revisions, while figure-specific input manifests identify the numerical sources.

Hardware, solver versions, per-pricing limits, the final integer-RMP cap and process scheduling can affect runtimes and incumbents. Logical work limits and deterministic task ordering improve control but do not guarantee bitwise equality across environments. Preserve solver statuses and configuration snapshots alongside new outputs. See the [protocol](experiments/README.md#4-campaigns-and-executable-configurations) for intrinsic stopping criteria.

## Markdown and rendering

The layout follows the owner's [integer-linear-programming-notes](https://github.com/flgkd/integer-linear-programming-notes), including its [Dantzig–Wolfe chapter](https://github.com/flgkd/integer-linear-programming-notes/blob/main/notes/08-dantzig-wolfe-decomposition.md): numbered conceptual sections, definitions near equations, display mathematics, compact pseudocode and relative document links. The wording and project-specific derivations here were independently authored.

Inline math uses dollar delimiters, with backtick protection where Markdown would alter the expression. Display math uses GitHub-supported `math` fences with trailing `\qquad \text{(number)}` labels and structural line breaks in long equations. References use ordinary section links and equation numbers local to each document. Static checks cover balanced delimiters, local link targets and image paths. PNG previews are rendered directly from the source PDFs. GitHub's Markdown API is checked for preservation of each expression, followed by a local MathJax browser preview at desktop widths. The API check covers native Markdown parsing; the local preview does not verify GitHub's live browser layout.

## Rights and licensing

Source code, algorithm implementations, executable and plotting scripts, tests, examples, and executable configurations are covered by the [MIT License](../LICENSE). Original documentation and figures are covered by [CC BY 4.0](../LICENSE-CONTENT), with attribution and figure exceptions specified there.

Third-party software and materials retain their respective licenses. Gurobi and its binaries are not covered by the repository's MIT License. Author portraits and unrelated manuscript assets are not included.
