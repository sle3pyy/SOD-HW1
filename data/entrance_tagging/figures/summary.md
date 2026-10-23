# Part B report statistics

Data: OpenStreetMap snapshot of 2026-10-20 (Geofabrik Portugal extract), Porto municipality. There is a single snapshot and no trajectory data, so there is no temporal dimension to analyse.

## Data inventory

| item | value |
|---|---|
| AOI area (km²) | 41.5 |
| buildings in AOI + 1 km | 59930 |
| public facilities in AOI + 1 km | 2451 |
| entrances in AOI + 1 km | 2069 |
| entrances: generic | 1221 |
| entrances: main | 292 |
| entrances: non_public_or_special | 556 |
| label buildings (public, exactly one main entrance) | 89 |
| prediction buildings (all public facilities in AOI) | 1803 |
| context: road main | 8237 |
| context: road pedestrian | 2061 |
| context: road service | 7503 |
| context: railway | 457 |
| context: bicycle_parking | 131 |
| context: landmark | 798 |
| context: post_box | 55 |
| context: obstacle barrier | 11949 |
| context: obstacle green | 8140 |
| context: obstacle water | 201 |
| label buildings used for training | 86 |

## Anomalies and data quality

| anomaly | value |
|---|---|
| buildings with more than one main entrance (excluded) | 21 |
|   of which public facilities | 15 |
| entrances not within 5 m of any building | 145 |
|   of which main entrances | 22 |
| multi-part footprints among labels | 4 |
| multi-part footprints among targets | 13 |
| labels skipped (entrance > 5 m from the sampled outline) | 3 |
| main entrance to footprint outline, median (m) | 0 |
| main entrance to footprint outline, 90th percentile (m) | 0.21 |
| main entrance to footprint outline, max (m) | 7.96 |

## Spatial distribution (Clark-Evans nearest-neighbour ratio)

| set | n | mean NN distance (m) | expected if random (m) | Clark-Evans R | z | standard distance (m) |
|---|---|---|---|---|---|---|
| labels | 89 | 235.3 | 341.4 | 0.689 | -5.61 | 2174.3 |
| targets | 1803 | 52.1 | 75.9 | 0.688 | -25.38 | 2608.6 |

R < 1: clustered, R = 1: random, R > 1: dispersed; |z| > 1.96 is significant at 5%.

## Building size (median)

| set | median area (m²) | median perimeter (m) |
|---|---|---|
| labels | 1003 | 172 |
| targets | 341 | 85 |

## Building size: labels vs targets (two-sample Kolmogorov-Smirnov)

| measure | KS statistic | p-value |
|---|---|---|
| area_m2 | 0.341 | 2.6e-09 |
| perimeter_m | 0.368 | 8.1e-11 |

## Class imbalance

| measure | value |
|---|---|
| samples in labelled buildings | 5608 |
| positive samples | 86 |
| positive share (%) | 1.53 |
| negatives per positive | 64.2 |
| samples per building, median | 55 |
| training samples after strong-negative sampling | 5494 |
| positive share after sampling (%) | 1.57 |

## Entrance cues: positive vs negative samples

| feature | median positive | median negative | Mann-Whitney p |
|---|---|---|---|
| cen_sort | 0.561 | 0.507 | 6.7e-01 |
| main_p_sort | 0.372 | 0.511 | 5.6e-04 |
| main_path_sort | 0.212 | 0.425 | 2.6e-05 |
| vis_mr_sort | 0.639 | 0.024 | 1.9e-11 |
| open_area_sort | 0.535 | 0.5 | 4.6e-02 |
| proportion | 0.083 | 0.25 | 1.3e-11 |

Rank features are a sample's rank in its building divided by the number of samples.

## Validation: distance error over the 86 labelled buildings (5-fold CV for the models)

| method | linear mean (m) | linear median (m) | linear ≤ 10 m (%) | linear ≤ 20 m (%) | path mean (m) | path median (m) | path ≤ 10 m (%) | no walking path |
|---|---|---|---|---|---|---|---|---|
| centroid-nearest sample | 50.9 | 37.4 | 22 | 34 | 56.1 | 30.8 | 31 | 41 |
| main-road-nearest sample | 40.1 | 25.3 | 22 | 44 | 44 | 25.9 | 17 | 20 |
| BRF | 22.1 | 12.3 | 47 | 62 | 23.4 | 12.2 | 46 | 7 |
| WRF | 27.3 | 16.7 | 40 | 53 | 31.8 | 16 | 39 | 7 |
| random sample (expected) | 48.1 | 40.8 | 11 | 21 | 46.8 | 35 | 12 | n/a |

Linear: distance along the outline between the predicted and the true entrance sample (Hu et al.: ~21 m). Path: walking distance outside the building (Hu et al.: ~22 m); buildings without a walking path are left out of the path columns.

## Validation: 95% bootstrap intervals (10000 building resamples)

| method | linear mean 95% CI (m) | linear median 95% CI (m) | path mean 95% CI (m) | path median 95% CI (m) |
|---|---|---|---|---|
| BRF | 16.4–28.4 | 6.1–18.3 | 17.1–30.7 | 8.7–19.4 |
| WRF | 20.8–34.8 | 10.2–23.1 | 23.1–41.2 | 10.5–25.7 |

## Tagged targets: confidence

| statistic | probability | margin |
|---|---|---|
| count | 1803 | 1803 |
| mean | 0.689 | 0.056 |
| std | 0.126 | 0.061 |
| min | 0.25 | 0 |
| 25% | 0.61 | 0.02 |
| 50% | 0.7 | 0.04 |
| 75% | 0.78 | 0.07 |
| max | 0.99 | 0.51 |
