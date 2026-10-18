# Edictai website

Static [Astro](https://astro.build) site for Edictai, served from GitHub Pages at `/statescope/`.

Pages are generated at build time from the dataset tables that `statescope build`
writes to `../build/csv/`. Set `EDICTAI_BUILD_DIR` to use a build directory elsewhere.

## Build

Requires Node 22.22 or later and the Python pipeline (`uv`).

```sh
# from the repository root: build the dataset
uv run statescope build

# then the site
cd site
npm ci
npm run build      # writes dist/
npm run preview    # serves dist/ at http://localhost:4321/statescope/
```

`npm run dev` starts a development server with live reload.

## Layout

- `src/lib/data.ts` reads the dataset: states, records, topics, counts.
- `src/styles/tokens.css` holds the colours, type and card tokens shared by every page; `base.css` the element defaults.
- `src/components/` holds the home page parts: `Card`, `WordmarkLogo`, `SectionRows`/`SectionRow` with the scene icons, and `ShapeField`, the canvas behind the card.
- `src/pages/` holds the routes: `/`, `/states/`, `/states/{code}/`, `/policies/`, `/legislation/{id}/`, `/documents/{id}/`, `/executive-actions/{id}/`, `/topics/`, `/compare/`, `/research/`, `/articles/`, `/about/`.
