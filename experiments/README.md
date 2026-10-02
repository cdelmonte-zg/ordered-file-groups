# Experiments: one variable at a time

Side experiments that change one property of the data and keep everything else
fixed: seed 9, two files of 300,000 rows drawn from the same pools, both files
sorted and overlapping, `target_partitions = 2`, fair pool of 128 MB, the
`original` binary (which on two groups produces the ordered plan, with
`PartiallySorted([0, 1])` on both aggregates and `preserve_order=true`), one
unrecorded warm-up and three recorded runs. Each directory holds the runner,
the SQL, every run's output and a `results.tsv`.

They were run on 2026-10-02 to find out why the data of round 4 makes the
ordered plan spill at 128 MB where the reporter's data of rounds 1 to 3 did not.
The two datasets have the same rows, the same scan volume (70 MB), the same
number of groups and the same plans; they differ in the cardinality of the sort
key prefix and in the length of the string codes.

## prefix-cardinality: the number of distinct (col_1, col_2) values

`--cluster-ms 1` gives every entity only its 5 to 10 cluster bases as
timestamps, about 200 distinct prefixes in all, like the reporter's data;
`--cluster-ms 500` gives up to 5,000 per entity, about 100,000 in all, like
round 4.

| cluster width | distinct prefixes | elapsed (3 runs) | final aggregate spills | repartition spills |
|---|---|---|---|---|
| 1 ms | few hundred | 0.614, 0.571, 0.599 | 14, 14, 14 | 4, 4, 4 |
| 500 ms | about 100,000 | 0.603, 0.610, 0.618 | 14, 15, 16 | 4, 4, 4 |

No difference. The hypothesis that the prefix cardinality drives the spills is
falsified.

## string-width: the length of the codes in col_1, col_3 and col_4

The reporter's data has `col_1` at 25 characters, `col_3` at 12 and `col_4` at
11; round 4 has 15, 14 and 13.

| variant | col_1 | col_3 | col_4 | elapsed (3 runs) | final aggregate spills | partial aggregate output, run 2 |
|---|---|---|---|---|---|---|
| baseline (round 4) | 15 | 14 | 13 | 0.620, 0.581, 0.633 | 15, 15, 14 | 468 MB |
| refs-short | 15 | 11 | 10 | 0.390, 0.407, 0.376 | 0, 0, 0 | 273 MB |
| all-short | 12 | 11 | 10 | 0.380, 0.351, 0.375 | 0, 0, 0 | 147 MB |
| all-long | 25 | 14 | 13 | 0.644, 0.634, 0.624 | 8, 12, 8 | 485 MB |
| refs-12 | 15 | 12 | 12 | 0.377, 0.396, 0.382 | 0, 0, 0 | |
| refs-13 | 15 | 13 | 13 | 0.605, 0.621, 0.596 | 14, 14, 8 | |

The spills of the final aggregate at 128 MB come and go with the length of
`col_3` and `col_4`, and the threshold is between 12 and 13 characters. The
length of `col_1` alone does not remove them (all-long against baseline), but it
adds to the volume: the output of the partial aggregate, as the metrics count
it, is 147 MB when all three codes fit in 12 characters, 273 MB when `col_1`
does not, 468 MB when none of the three does. The reporter's data, with only
`col_1` above 12 characters, sits at 276 MB in round 3.

Twelve bytes is the inline limit of Arrow's string view arrays: a value of up to
12 bytes is stored in the 16-byte view itself, a longer one in a separate data
buffer that the view points into (`arrow-array`, `byte_view_array.rs`), and
DataFusion reads Parquet strings as view arrays by default
(`schema_force_view_types = true`). That the threshold coincides is measured; how
a longer string turns into about twice the bytes per batch in the operators
above the scan, and into spills, was not traced in the code.
