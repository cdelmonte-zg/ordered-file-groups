from pathlib import Path
import pyarrow as pa
import pyarrow.parquet as pq

source = pq.read_table(
    "/tmp/df-16919-sorted-medium/reproducible_data_0.parquet"
).slice(0, 50_000)
field = source.schema.field("col_6")
index = source.schema.get_field_index("col_6")
values = source.column("col_6").to_pylist()
output = Path("/tmp/df-16919-overlap-medium-12")
output.mkdir(parents=True, exist_ok=True)

for i in range(12):
    col_6 = pa.array(
        [f"{i:02d}_{value}" for value in values],
        type=field.type,
    )
    table = source.set_column(index, field, col_6)
    pq.write_table(
        table,
        output / f"reproducible_data_{i}.parquet",
        compression="zstd",
    )
