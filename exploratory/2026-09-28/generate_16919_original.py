# Part of https://github.com/apache/datafusion/discussions/16776

# Run the code:
# python3 -m venv venv
# source venv/bin/activate
# pip install pandas pyarrow
# python3 generate_fake_data_anon_v4.py --rows 1250000 --files 15`

# Remove after use:
# deactivate
# rm -rf venv/


"""Generate synthetic time series data for DataFusion deduplication testing."""
import argparse, os, random, string, time
from datetime import datetime, timedelta
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

def generate_random_string(length=10):
    """Generate random string of fixed length."""
    letters = string.ascii_letters + string.digits + "-_"
    return ''.join(random.choice(letters) for _ in range(length))

def initialize_pools(size):
    """Pre-generate pools of values to use for data generation."""
    # Create entity IDs (col_1) with consistent format
    entity_ids = []
    for _ in range(30):  # Limited number of unique entities
        prefix = f"{random.randint(1000000, 9999999)}"
        mid = f"{random.randint(10, 99)}-{random.choice('ABCDE')}"
        suffix = f"{random.choice('XYZ')}{random.randint(10, 19)}{random.choice('ABCDE')}{random.randint(1000000, 9999999)}"
        entity_ids.append(f"{prefix}-{mid}--{suffix}")
    
    # Generate reference codes (col_3)
    reference_codes = []
    for _ in range(20):
        prefix = f"{random.randint(1000000, 9999999)}"
        suffix = f"{random.randint(10, 99)}-{random.choice('ABCDEFGHIJKLMNOPQRST')}"
        reference_codes.append(f"{prefix}-{suffix}")
    
    # Generate instance IDs (col_4)
    instance_ids = []
    for _ in range(50):
        instance = f"{random.choice('XYZ')}{random.randint(10, 19)}{random.choice('ABCDE')}{random.randint(1000000, 9999999)}"
        instance_ids.append(instance)
    
    # Generic categories (col_5)
    categories = ["CAT_1", "CAT_2", "CAT_3", "CAT_4", "CAT_5", "CAT_6"]
    
    # Generate metric names (col_6) with prefixes matching categories
    metric_names = []
    
    # Create metrics for each category
    prefixes = {
        "CAT_1": "M1",
        "CAT_2": "M2",
        "CAT_3": "M3",
        "CAT_4": "M4", 
        "CAT_5": "M5",
        "CAT_6": "M6"
    }
    
    # Generate metrics for each category
    for cat, prefix in prefixes.items():
        for i in range(1, 11):  # 10 metrics per category
            metric_names.append(f"{prefix}_{i:02d}")
    
    # Add some special cross-category metrics
    for i in range(1, 6):
        metric_names.append(f"METRIC_{i:02d}")
    
    # Generate timestamps clustered around specific points
    end_time = int(datetime.now().timestamp() * 1000)
    start_time = end_time - (7 * 24 * 60 * 60 * 1000)  # 7 days ago
    
    # Create clusters of timestamps to match sampling patterns
    timestamps = []
    for _ in range(50):  # 50 time clusters
        base_ts = random.randint(start_time, end_time)
        for offset in range(0, 500, 1):  # Milliseconds of offset
            timestamps.append(base_ts + offset)
    
    # Generate context values (col_7)
    context_values = ["CONTEXT_A", "CONTEXT_B", "CONTEXT_C", "CONTEXT_D", 
                      "CONTEXT_E", "CONTEXT_F", "CONTEXT_G", "CONTEXT_H"]
    
    return {
        "col_1": entity_ids,
        "col_2": timestamps,
        "col_3": reference_codes,
        "col_4": instance_ids, 
        "col_5": categories,
        "col_6": metric_names,
        "col_7": context_values
    }

def generate_time_series_data(num_rows, pools):
    """Generate time series data with realistic patterns."""
    # Initialize data dictionary
    data = {col: [] for col in ["col_1", "col_2", "col_3", "col_4", "col_5", "col_6", "col_7", "col_8"]}
    
    # Generate data with realistic clustering patterns
    entity_timestamps = {}  # Track timestamps per entity
    
    # Set up the relationship between categories and metrics
    category_to_metrics = {
        "CAT_1": [m for m in pools["col_6"] if m.startswith("M1_")],
        "CAT_2": [m for m in pools["col_6"] if m.startswith("M2_")],
        "CAT_3": [m for m in pools["col_6"] if m.startswith("M3_")],
        "CAT_4": [m for m in pools["col_6"] if m.startswith("M4_")],
        "CAT_5": [m for m in pools["col_6"] if m.startswith("M5_")],
        "CAT_6": [m for m in pools["col_6"] if m.startswith("M6_")]
    }
    
    # Cross-category metrics can be used by any category
    cross_metrics = [m for m in pools["col_6"] if m.startswith("METRIC_")]
    for cat in category_to_metrics:
        category_to_metrics[cat].extend(cross_metrics)
    
    for _ in range(num_rows):
        # Pick an entity
        entity_id = random.choice(pools["col_1"])
        
        # Get or create timestamps for this entity
        if entity_id not in entity_timestamps:
            # Pick 5-10 timestamp clusters for this entity
            num_clusters = random.randint(5, 10)
            timestamp_clusters = sorted(random.sample(pools["col_2"], num_clusters))
            entity_timestamps[entity_id] = timestamp_clusters
        
        # Pick a timestamp from this entity's clusters
        timestamp = random.choice(entity_timestamps[entity_id])
        
        # For the same entity/timestamp, randomly select other attributes
        category = random.choice(pools["col_5"])
        
        # Select a metric appropriate for this category
        metric_name = random.choice(category_to_metrics.get(category, pools["col_6"]))
            
        # Add the row
        data["col_1"].append(entity_id)
        data["col_2"].append(timestamp)
        data["col_3"].append(random.choice(pools["col_3"]))
        data["col_4"].append(random.choice(pools["col_4"]))
        data["col_5"].append(category)
        data["col_6"].append(metric_name)
        data["col_7"].append(random.choice(pools["col_7"]) if random.random() > 0.05 else None)
        
        # Generate values using metric-specific ranges to create variation
        # But keep the ranges generic without suggesting specific physical quantities
        metric_id = int(metric_name.split('_')[-1]) if '_' in metric_name else 0
        
        if metric_id % 5 == 0:
            # Large range values
            data["col_8"].append(random.uniform(-50000, 50000))
        elif metric_id % 5 == 1:
            # Medium positive values
            data["col_8"].append(random.uniform(0, 1000))
        elif metric_id % 5 == 2:
            # Small precise values
            data["col_8"].append(random.uniform(-1, 1))
        elif metric_id % 5 == 3:
            # Binary-like values
            data["col_8"].append(random.choice([0, 1]))
        else:
            # Medium range values
            data["col_8"].append(random.uniform(-100, 100))
            
    return pd.DataFrame(data)

def save_with_schema(df, file_path, compression):
    """Save dataframe to parquet with explicit schema and large row groups."""
    # Define schema with proper NOT NULL constraints
    schema = pa.schema([
        ('col_1', pa.string(), False),  # NOT NULL
        ('col_2', pa.int64(), False),   # NOT NULL
        ('col_3', pa.string(), True),   # NULLABLE
        ('col_4', pa.string(), True),   # NULLABLE
        ('col_5', pa.string(), True),   # NULLABLE
        ('col_6', pa.string(), False),  # NOT NULL
        ('col_7', pa.string(), True),   # NULLABLE
        ('col_8', pa.float64(), True),  # NULLABLE
    ])
    
    # Convert pandas DataFrame to Arrow Table with explicit schema
    table = pa.Table.from_pandas(df, schema=schema)
    
    # Write with optimized settings for performance
    pq.write_table(
        table, 
        file_path,
        compression=compression,
        write_statistics=True,
        use_dictionary=True,
        row_group_size=1048576,  # Default DataFusion row group size
        data_page_size=1048576   # Default DataFusion data page size
    )

def main():
    """Parse arguments and generate data."""
    parser = argparse.ArgumentParser(description='Generate synthetic data for deduplication testing')
    parser.add_argument('--rows', type=int, default=1_250_000, help='Number of rows per file')
    parser.add_argument('--files', type=int, default=15, help='Number of output parquet files')
    parser.add_argument('--output-dir', type=str, default='datav4', help='Directory to save output files')
    parser.add_argument('--compression', type=str, default='zstd', 
                       choices=['zstd', 'snappy', 'gzip', 'none'], 
                       help='Compression algorithm for parquet files')
    args = parser.parse_args()
    
    # Create output directory
    os.makedirs(args.output_dir, exist_ok=True)
    
    # Set up compression
    compression = args.compression if args.compression != 'none' else None
    compression_info = f" with {compression} compression" if compression else " without compression"
    
    # Initialize value pools
    print(f"Initializing value pools...")
    pools = initialize_pools(1000)
    
    # Calculate total rows to generate
    total_rows = args.rows * args.files
    start_time = time.time()
    print(f"Generating {total_rows:,} total rows{compression_info}")
    print("Using schema with NOT NULL constraints on col_1, col_2, and col_6")
    
    # Process files individually to avoid memory issues
    for i in range(args.files):
        file_start = time.time()
        print(f"Generating file {i+1}/{args.files}: {args.rows:,} rows...")
        
        # Generate time series data
        df = generate_time_series_data(args.rows, pools)
        
        # Sort only partially - this forces external sorting during queries
        print(f"Partial sorting of data to reproduce memory-intensive query execution...")
        
        # Split data by entity ID first
        entities = df['col_1'].unique()
        sorted_dfs = []
        
        # Sort each entity's data differently to ensure external sorting is needed
        for j, entity in enumerate(entities):
            entity_df = df[df['col_1'] == entity].copy()
            # Some sorted ascending, some descending to create the need for external sorting
            if j % 2 == 0:
                entity_df = entity_df.sort_values(by=['col_1', 'col_2'], ascending=[True, True])
            else:
                entity_df = entity_df.sort_values(by=['col_1', 'col_2'], ascending=[True, False])
            sorted_dfs.append(entity_df)
        
        # Combine the sorted entity dataframes
        df = pd.concat(sorted_dfs)
        
        # Save to parquet with schema enforcement
        file_path = os.path.join(args.output_dir, f"reproducible_data_{i}.parquet")
        print(f"Saving {len(df):,} rows to {file_path}{compression_info}...")
        
        try:
            save_with_schema(df, file_path, compression)
        except Exception as e:
            if "zstd" in str(e) and compression == 'zstd':
                print(f"Warning: zstd compression not available. Falling back to snappy compression.")
                save_with_schema(df, file_path, 'snappy')
            else:
                raise
        
        file_duration = time.time() - file_start
        print(f"File {i+1}/{args.files} completed in {file_duration:.1f} seconds ({args.rows/file_duration:.1f} rows/sec)")
        
        # Help free memory
        del df
    
    total_duration = time.time() - start_time
    print(f"All done! Generated {args.files} files with {args.rows:,} rows each ({total_rows:,} total rows)")
    print(f"Total time: {total_duration:.1f} seconds ({total_rows/total_duration:.1f} rows/sec)")
    print(f"Files are structured to reproduce memory-intensive query patterns")

if __name__ == "__main__":
    main()
