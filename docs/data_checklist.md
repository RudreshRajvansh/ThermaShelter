# Real data still needed — copyable checklist

Paste this straight to Mahi. Every line traces to a warning the engine raises on a real
run, so nothing here is invented pressure. Tick them off and the warnings go away.

Run `python scripts/start.py --check` at any time to see which of these are still open.

---

## Copy from here

```
LAYER 0 — DATA STILL NEEDED
Every item below is something the tool currently warns about on every single run.

=== BLOCKING THE HEADLINE ===

[ ] 1. Diesel or kerosene price
      - delivered price per litre, at Leh or at the forward post
      - which fuel a post actually burns
      - heater efficiency to assume (a bukhari is not a modern space heater)
      - source, so it survives a judge asking
      Why: the fuel price is currently zero, so "diesel avoided per winter" —
      the number the whole problem statement is about — renders as withheld.
      Engine warning: fuel_price_unset

[ ] 2. Confirm or overrule the clay brick correction
      Layer 0 gave k = 0.17 W/mK and cp = "330-440" J/kgK at 1980 kg/m3.
      Both fail a physical plausibility check. I substituted ASHRAE values
      (k = 0.90, cp = 800) because this is the BASELINE wall and a five times
      too low conductivity makes current construction look insulated, shrinking
      every improvement we claim.
      Need: either confirm ASHRAE, or send the source the 0.17 came from.
      If it is a hollow or perforated block rather than solid brick, then the
      density is what is wrong, not the conductivity.
      Engine warning: material_properties_implausible

=== COSTS: NO COST COLUMN EXISTS AT ALL ===

[ ] 3. Delivered cost per material, as extra columns on materials.csv
      cost_inr_m3            INR per cubic metre, delivered not ex-works
      locally_available      true / false
      transport_multiplier   >= 1.0, only if the cost above is ex-works
      mason_skill            unskilled | semiskilled | skilled | specialist
      cost_source            where the rate came from
      cost_source_status     verified | pending
      Ideal: CPWD or BRO schedule of rates, Leh circle. A named supplier
      quotation is fine too.
      Why: cost is one of the three objectives the optimiser minimises, and a
      named column in the comparison table. All 22 records are currently
      flagged because no price has a source.
      Engine warning: capital_cost_unsourced

=== MATERIALS I HAD TO INVENT ===

[ ] 4. Ten materials not in the delivery, currently ASHRAE placeholders:
      rammed_earth      <- matters most, this is the traditional baseline
      mud_brick         <- matters most
      stone_granite     <- matters most
      mud_plaster
      cement_plaster
      eps_thermocol
      xps_board
      rockwool
      gi_sheet
      gravel_bed
      Need per material: conductivity, density, specific heat, source.
      The three marked "matters most" are what Ladakhi construction is
      actually built from. Generic ASHRAE "rammed earth" is not the same as
      Ladakhi rammed earth at 3200 m.

[ ] 5. Three glazing systems, none supplied:
      single_clear_4mm
      double_clear_6_12_6
      double_lowe_argon
      Need per system: U-value, SHGC, visible transmittance, cost per m2.
      A manufacturer datasheet for what is actually procurable in Leh beats a
      textbook table, because glazing is one of the levers the optimiser moves.
      Engine warning: materials_unsourced

[ ] 6. Solar absorptance — not supplied for anything
      Every material is sitting on the schema default right now.
      If you measure only ONE optical property, measure this one, for the
      Trombe wall absorber finish we intend to use. A Trombe wall is entirely
      about its absorptance; it is the number that decides whether that whole
      design works.

=== CITATIONS ===

[ ] 7. Reference design citations — all three are uncited:
      current_practice_masonry   <- most important citation in the project
      traditional_mud_stone
      modern_insulated_prefab
      Why: every improvement we report is a difference against current
      practice. If a judge asks "compared to what?", the honest answer today
      is "an assembly we assumed."
      Need: a DRDO or BRO standard barrack drawing, or a photo plus a
      described build-up of a real one.
      Engine warning: reference_designs_uncited

[ ] 8. Comfort model coefficients
      imac_2016_nv is marked pending, and imac_r_2022_nv is a placeholder
      holding EXACTLY the same numbers, so choosing between them cannot change
      a single output. Need intercept, slope and band half width from the
      residential IMAC paper (Manu and others, 2022).
      Engine warnings: comfort_model_unverified, comfort_model_duplicates_another

=== LAYER 1, WHENEVER IT EXISTS ===

[ ] 9. Standard 140 test case results
[ ] 10. ANSYS thermal bridge psi values (wall-roof, wall-floor junctions)
[ ] 11. ANSYS fluid dynamics export, if that run happens
[ ] 12. Any calibration data
      All as files under data/validation/.

=== SMALL FIXES AT SOURCE ===

[ ] 13. materials.csv has a Cp value written as the range string "330-440".
        My loader takes the midpoint. One number, or explicit low/high
        columns, is cleaner.
[ ] 14. pcm.csv repeats its header row inside the data. I filter it, but
        better fixed at source.

=== FORMAT ===

Files, not an API. Same zip layout as before is perfect. The demo has to run
with the network cable pulled out, and a frozen file is what makes a result
reproducible when a judge asks us to run it twice.

I ingest it with:
    python scripts/ingest_layer0.py --source path/to/layer_0 --out data
It prints exactly what it took from you, what it filled in, and what it corrected.
```

## Copy to here

---

## What is already done and needs nothing

Do not re-send these; they went in clean.

| item | state |
| --- | --- |
| Leh TMYx weather year | 8,760 h, zero loader warnings, altitude pressure check passed at 2.1 % |
| SRTM terrain mesh | 2,601 points, 10 km across, ridges to 1,151 m |
| 36 direction horizon | south open at 1.9°, east blocked to 12.2° |
| Ground temperatures | EPW header, 0.5 m depth |
| Four Rubitherm PCMs | RT21, RT21HC, RT25, RT25HC |
