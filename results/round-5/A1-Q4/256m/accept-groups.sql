SET datafusion.execution.target_partitions = 2;
SET datafusion.execution.split_file_groups_by_statistics = true;

CREATE EXTERNAL TABLE example (
    col_1 VARCHAR NOT NULL,
    col_2 BIGINT NOT NULL,
    col_3 VARCHAR,
    col_4 VARCHAR,
    col_5 VARCHAR,
    col_6 VARCHAR NOT NULL,
    col_7 VARCHAR,
    col_8 DOUBLE
)
WITH ORDER (col_1 ASC, col_2 ASC)
STORED AS PARQUET
LOCATION '/tmp/df-16919-partial-12-depth-4/';

EXPLAIN ANALYZE
SELECT col_3, col_4, col_5, col_6, count(*) AS n
FROM example
GROUP BY col_3, col_4, col_5, col_6;
