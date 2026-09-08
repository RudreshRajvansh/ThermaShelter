# Layer 0 — what landed, what I changed, and what I still need

Rudresh → Mahi. Everything below is checkable against `data/layer0_manifest.json`,
which the ingest script writes on every run.

---

## What landed and works

I ingested the zip with `scripts/ingest_layer0.py`. Four things went straight in with
no argument from me:

- **The weather file.** `IND_JK_Leh.427053_TMYx.2011-2025.epw` loads with zero warnings.
  8,760 hours, annual mean −0.84 °C, 6,878 heating degree days, no cooling degree days.
  The engine checks the pressure column against the site elevation, because at 3,256 m a
  sea level pressure column would make every air related heat transfer roughly a third
  too large and EnergyPlus would not warn about it. Yours reads 69,287 Pa against 67,857
  expected — 2.1 % apart. You got the altitude correction right, and that is the single
  easiest thing to get wrong at this altitude.
- **The terrain.** The SRTM mesh and the 36 direction horizon both went in. The
  simulation shades with the horizon table and the 3D view stands on the mesh, so what
  blocks the sun on screen is what blocked it in the run. South is open at 1.9° and east
  is blocked to 12.2°, which is a real result and almost no other team will have it.
- **The ground temperatures.** I used the EPW header values at 0.5 m rather than the NASA
  POWER TSOIL1 series. Reason below — flag it if you disagree.
- **The four Rubitherm PCMs.**

## One thing I changed, and I want you to confirm or overrule it

**Clay brick.** The row gives k = 0.17 W/mK and cp = "330-440" J/kgK at 1,980 kg/m³.
Both fail the engine's physical plausibility check. Fired clay brick at that density
conducts near 0.9 W/mK and stores near 800 J/kgK — the given conductivity is about five
times too low, and the specific heat is closer to a metal than a ceramic.

This matters more than any other row in the file, because `brick_solid` is the wall of
the **current practice baseline**. Every headline number we report is a difference
against that baseline. A five times too low conductivity makes existing construction look
well insulated and shrinks every improvement we claim. It is the one error that would
make us look like we are underselling our own result to a DRDO panel.

I substituted ASHRAE values (k = 0.90, cp = 800) and recorded the substitution in
`layer0_manifest.json` rather than editing quietly. **Please either confirm the ASHRAE
values or send me the source the 0.17 came from** — if it is a hollow or perforated
block rather than solid brick, then 0.17 could be right for the assembly and the density
is what needs changing instead. Either way I need to know which.

Two smaller notes on the same file:

- The `Cp_J_kgK` column has one value written as a range string, `"330-440"`. My loader
  takes the midpoint. A single number, or two explicit low/high columns, is cleaner.
- `pcm.csv` has its header row repeated inside the data. I filter it, but worth fixing at
  source.

---

## What I need, in priority order

### 1. The diesel price — this is the single most important missing number

One number, and it unblocks the headline of the whole project.

The build guide is blunt about what we are judged on: *"Put diesel and rupees at the
top. Temperature is how we got there. Fuel not burned is what DRDO is buying."* The
target sentence is *"avoids N litres of diesel per winter compared to current
construction."*

Right now the fuel price is set to zero, deliberately, so that figure renders as
**withheld** rather than as a plausible invented number. Which means the one metric the
problem statement is actually about is currently blank on screen.

I need:

- delivered price of diesel (or kerosene, whichever a forward post actually burns) per
  litre, at Leh or at the post
- the heater efficiency to assume — a bukhari and a modern space heater are not the same
  number, and the answer moves the litres proportionally
- the source, so it survives a judge asking where it came from

If you send me nothing else on this list, send me this.

### 2. Delivered material costs

`materials.csv` has no cost, availability or mason skill columns at all, and your
`source.txt` says these must come from locally sourced values. The team split names this
as yours: *"the Ladakh material costs and buildability constraints."*

This is second rather than first because it is not the headline — but it is not optional
either. Capital cost is one of the **three objectives the optimiser minimises**
(degree hours outside the comfort band, cost, and margin against the worst design day),
and it is a named column in the comparison table, which the guide says the problem
statement asks for by name. Without real rates the Pareto front has a decorative axis:
the shape of the trade off is wrong, so the design it recommends may be wrong.

Every rupee figure is currently built on **prices I invented** as placeholders to make
the code path work. They are labelled as such — the database carries a separate
`cost_source` field per record, the engine raises a `capital_cost_unsourced` warning on
every run, and the interface shows it. Nothing is being passed off as real. All 22
records are flagged, because Layer 0 supplied no cost column at all.

What I need, per material, delivered to Leh:

| column | unit | note |
| --- | --- | --- |
| `cost_inr_m3` | INR per m³ | delivered, not ex-works |
| `locally_available` | true/false | sourced within Ladakh or trucked in |
| `transport_multiplier` | ≥ 1.0 | if the cost above is ex-works instead |
| `mason_skill` | unskilled / semiskilled / skilled / specialist | who can lay it |
| `cost_source` | text | where the rate came from |
| `cost_source_status` | verified / pending | |

A CPWD or BRO schedule of rates for the Leh circle would be ideal. A quotation from one
supplier is fine too — anything with a name on it beats a good guess.

### 3. Material properties I had to fill in myself

Ten materials and all three glazing systems were not in the delivery. I filled them from
the ASHRAE Handbook of Fundamentals so the reference designs could be built at all. Each
is marked `pending` and appears in the `provenance_gaps` list.

Materials I invented: `rammed_earth`, `mud_brick`, `mud_plaster`, `cement_plaster`,
`stone_granite`, `eps_thermocol`, `xps_board`, `rockwool`, `gi_sheet`, `gravel_bed`.

Glazing: `single_clear_4mm`, `double_clear_6_12_6`, `double_lowe_argon` — U value, SHGC
and visible transmittance. A manufacturer datasheet for whatever is actually procurable
in Leh beats a textbook table here, because the glazing choice is one of the variables
the optimiser moves.

The Ladakhi ones matter most: **rammed earth, mud brick and local stone**. Those are what
the traditional baseline is actually built from, and a generic ASHRAE number for
"rammed earth" is not the same as Ladakhi rammed earth at 3,200 m.

### 4. Solar absorptance

Not supplied for anything, so every material is sitting on the schema default. Surface
colour changes solar gain on an exposed wall by a large fraction, and a Trombe wall
absorber is *entirely* about its absorptance — it is the one number that decides whether
that design works. If you can only measure one optical property, measure that one, for
the absorber plate finish we actually intend to use.

### 5. The comfort model coefficients

`imac_2016_nv` carries the widely quoted IMAC relation but is marked pending, and
`imac_r_2022_nv` is a placeholder holding **exactly the same numbers**. The engine now
warns about this outright: choosing between the two cannot change a single output. If you
have the residential IMAC paper, I need its intercept, slope and band half width.

### 6. Reference design citations

All three references — `current_practice_masonry`, `traditional_mud_stone`,
`modern_insulated_prefab` — are uncited. The current practice assembly is the most
important citation in the whole project, because every improvement we claim is measured
against it. If a judge asks "compared to what?", right now the honest answer is "an
assembly we assumed". A DRDO or BRO standard barrack drawing, or a photograph and a
description of a real one, would fix it.

### 7. Layer 1 evidence

Whenever it exists: Standard 140 results, the ANSYS thermal bridge psi values, and any
calibration data, as files under `data/validation/`.

---

## Format

Files, not an API — the demo has to run with the network cable pulled out, and a frozen
file is what makes a result reproducible when a judge asks us to run it twice. Same zip
layout as before is perfect. `scripts/ingest_layer0.py` takes it directly:

```bash
python scripts/ingest_layer0.py --source path/to/layer_0 --out data
```

It prints exactly what it took from you, what it had to fill in, and what it corrected.

## Ground temperature — the one judgement call I made

The delivery has two sources and they disagree:

- EPW header, 0.5 m: −10.9, −11.9, −10.1, −7.4, −0.3, 5.3, 9.0, 10.1, 8.2, 3.9, −1.8, −7.1 °C
- NASA POWER TSOIL1, 2015–2024 mean: −8.9, −8.5, −7.4, −2.4, 2.8, 7.7, 12.3, 11.9, 7.2, 0.5, −4.4, −8.8 °C

I used the EPW header, because it is derived from the same weather year the simulation
runs against, so it is internally consistent with it. Your own `source.txt` notes the
MERRA-2 grid cell sits at 4,533 m against the site's 3,256 m, so the POWER series is not
describing this site's soil. Tell me if you would rather I used POWER, or something
measured.
