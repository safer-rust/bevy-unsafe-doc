# bevy-unsafe-doc
Extract all public unsafe APIs (unsafe functions and unsafe traits) from the **Bevy game engine workspace**, and save the results to a static HTML table.

# Bevy Unsafe API Explorer

Extract and audit all public unsafe APIs (`unsafe fn` and `unsafe trait`) from the Bevy game engine workspace.
👉 [Live Documentation (Auto-updated Daily)](https://drasu33.github.io/bevy-unsafe-doc/)

## Goal

The script scans a local clone of the Bevy repository via rustdoc JSON, collects every item that is both `pub` and `unsafe`, and writes a five-column HTML table:

| Column | Content |
|--------|---------|
| (drag handle) | grab handle for reordering rows |
| Module | module path, e.g. `bevy_ptr` |
| API    | full item path linked to **docs.rs** |
| Safety doc | text from the `# Safety` section of the item's docs |
| Confirmed ✓ | checkbox to mark an API as reviewed |

## Prerequisites

1. **Rust nightly toolchain**:
   ```sh
   rustup toolchain install nightly
   ```
2. **A local clone of Bevy** (a shallow clone is recommended for speed):
   ```sh
   git clone --depth 1 https://github.com/bevyengine/bevy.git bevy_src
   ```
3. **Python 3** (3.8 or newer, no extra packages required).

## Usage

Run the script from your repository root, explicitly providing the path to the Bevy source and your desired output file:

```sh
python3 scripts/extract_bevy.py --bevy-dir ./bevy_src --output ./docs/index.html
```

This will:
1. Pass the `RUSTDOCFLAGS` environment variable to enable JSON output.
2. Run `cargo +nightly doc --workspace --no-deps` inside the provided Bevy directory.
3. Parse each generated JSON file (filtering for crates prefixed with `bevy`) and collect public unsafe items.
4. Write the interactive HTML table to the specified `--output` path.
5. Print the number of items parsed per crate.

## Continuous Integration (GitHub Actions)

This project uses a modern artifact-based deployment pipeline. The site is **not** served from a committed `docs/` folder on the `main` branch. 

Instead, the [Generate and Deploy Bevy Unsafe Docs](.github/workflows/deploy.yml) workflow runs automatically every day at midnight UTC (or manually via workflow dispatch). It performs the following on a cloud runner:
1. Performs a shallow clone of the latest Bevy `main` branch.
2. Compiles the rustdoc JSON.
3. Generates the HTML artifact.
4. Deploys it directly to GitHub Pages' CDN nodes, keeping your Git commit history completely clean.

## Enabling GitHub Pages

Since the CI workflow uses Artifacts, you must configure your repository settings accordingly:

1. Go to **Settings → Pages** in your repository.
2. Under **Build and deployment → Source**, change the dropdown from "Deploy from a branch" to **GitHub Actions**.

Once the workflow completes its first run, your site will be live at your standard GitHub Pages URL.

## Notes / Caveats

- **Nightly required**: rustdoc JSON (`-Z unstable-options --output-format json`) is a nightly-only unstable feature.
- **AST Parsing Limits**: This tool currently relies on Python dictionaries to parse the compiler's JSON output. It effectively captures standard `pub unsafe fn` and `pub unsafe trait` definitions but may miss complex macro-generated unsafe implementations (like those produced by `#[derive(Component)]`). 
- **Build Time**: The first run is significantly slower (can take several minutes) because `cargo` must analyze and document the entire Bevy workspace. Subsequent runs on the same source directory will reuse the build cache.