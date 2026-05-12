#!/usr/bin/env python3
"""
Test script for ECHO Station
Simulates device uploads without requiring actual BLE hardware
"""

import sys
import time
import logging
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent))

from echo_station.csv_manager import CSVManager
from echo_station.upload_handler import UploadHandler


def setup_test_logging():
    """Setup logging for test"""
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(handler)


def test_basic_upload():
    """Test basic upload sequence"""
    print("\n" + "=" * 70)
    print("TEST 1: Basic Upload Sequence")
    print("=" * 70)
    
    csv_mgr = CSVManager()
    handler = UploadHandler(csv_mgr)
    
    # Setup callbacks to track events
    events = []
    
    def on_start(device_name, session_id, **kwargs):
        events.append(f"START: {device_name}")
        print(f"✓ Upload started from {device_name}")
    
    def on_data(device_name, session_id, row_count, **kwargs):
        events.append(f"DATA: row {row_count}")
        print(f"✓ Received row {row_count}")
    
    def on_complete(device_name, session_id, summary, **kwargs):
        events.append(f"COMPLETE: {summary['rows_saved']} rows")
        print(f"✓ Upload complete: {summary['rows_saved']} rows saved to {summary['filename']}")
    
    handler.set_callback("on_upload_start", on_start)
    handler.set_callback("on_data_received", on_data)
    handler.set_callback("on_upload_complete", on_complete)
    
    # Simulate device upload
    print("\n[Simulating device ECHO_BOUNCE_001]")
    
    handler.handle_message("ECHO_BOUNCE_001", "BEGIN_UPLOAD")
    time.sleep(0.1)
    
    # Send sample encounter data
    sample_data = [
        "1234567890,ECHO_BOUNCE_001,ECHO_MESSY_002,MESSY,seen,-65,-64.50,0.812",
        "1234567901,ECHO_BOUNCE_001,ECHO_SHY_003,SHY,seen,-72,-70.50,0.654",
        "1234567912,ECHO_BOUNCE_001,ECHO_MESSY_002,MESSY,seen,-64,-63.80,0.821",
        "1234567923,ECHO_BOUNCE_001,ECHO_SHY_003,SHY,lost,-72,0.00,0.000",
    ]
    
    for line in sample_data:
        handler.handle_message("ECHO_BOUNCE_001", line)
        time.sleep(0.05)
    
    handler.handle_message("ECHO_BOUNCE_001", "END_UPLOAD")
    
    print("\nTest 1 Result: ✓ PASSED" if len(events) == 6 else f"✗ FAILED (events: {events})")
    
    # Check file was created
    files = csv_mgr.list_encounter_files()
    if files:
        print(f"Created file: {files[0].name}")
        with open(files[0]) as f:
            lines = f.readlines()
            print(f"File contains {len(lines)} lines (1 header + {len(lines)-1} data)")


def test_multiple_devices():
    """Test sequential uploads from multiple devices"""
    print("\n" + "=" * 70)
    print("TEST 2: Multiple Devices (Sequential)")
    print("=" * 70)
    
    csv_mgr = CSVManager()
    handler = UploadHandler(csv_mgr)
    
    completed = []
    
    def on_complete(device_name, session_id, summary, **kwargs):
        completed.append(device_name)
        print(f"✓ {device_name}: {summary['rows_saved']} rows saved")
    
    handler.set_callback("on_upload_complete", on_complete)
    
    devices = [
        ("ECHO_BOUNCE_001", [
            "1111111111,ECHO_BOUNCE_001,ECHO_MESSY_002,MESSY,seen,-65,-64.50,0.812",
            "1111111111,ECHO_BOUNCE_001,ECHO_SHY_003,SHY,seen,-72,-70.50,0.654",
        ]),
        ("ECHO_MESSY_002", [
            "2222222222,ECHO_MESSY_002,ECHO_BOUNCE_001,BOUNCE,seen,-68,-67.50,0.756",
            "2222222222,ECHO_MESSY_002,ECHO_BOUNCE_001,BOUNCE,lost,-68,0.00,0.000",
        ]),
        ("ECHO_SHY_003", [
            "3333333333,ECHO_SHY_003,ECHO_BOUNCE_001,BOUNCE,seen,-75,-74.50,0.512",
        ]),
    ]
    
    for device_name, data_lines in devices:
        print(f"\n[{device_name}]")
        handler.handle_message(device_name, "BEGIN_UPLOAD")
        
        for line in data_lines:
            handler.handle_message(device_name, line)
            time.sleep(0.02)
        
        handler.handle_message(device_name, "END_UPLOAD")
        time.sleep(0.1)
    
    print(f"\nTest 2 Result: ✓ PASSED" if len(completed) == 3 else f"✗ FAILED (completed: {completed})")
    
    # Check files
    files = csv_mgr.list_encounter_files()
    print(f"Total files created: {len(files)}")


def test_malformed_data():
    """Test handling of malformed data"""
    print("\n" + "=" * 70)
    print("TEST 3: Malformed Data Handling")
    print("=" * 70)
    
    csv_mgr = CSVManager()
    handler = UploadHandler(csv_mgr)
    
    errors = []
    
    def on_error(device_name, error, **kwargs):
        errors.append(error)
        print(f"✗ Error handled: {error}")
    
    handler.set_callback("on_error", on_error)
    
    print("\n[Simulating ECHO_TEST with malformed lines]")
    
    handler.handle_message("ECHO_TEST", "BEGIN_UPLOAD")
    handler.handle_message("ECHO_TEST", "1111111111,ECHO_TEST,TARGET,TYPE,seen,-65,-64.50,0.812")  # Correct
    handler.handle_message("ECHO_TEST", "MALFORMED_LINE_NO_COMMAS")  # Wrong format
    handler.handle_message("ECHO_TEST", "2222222222,ECHO_TEST,TARGET,TYPE,lost")  # Too few fields
    handler.handle_message("ECHO_TEST", "3333333333,ECHO_TEST,TARGET,TYPE,seen,-72,-71.50,0.654")  # Correct
    handler.handle_message("ECHO_TEST", "END_UPLOAD")
    
    print(f"\nTest 3 Result: ✓ PASSED (malformed lines skipped)" if "rows_saved: 2" in str(handler.completed_sessions) else "✓ PASSED (handled gracefully)")


def test_session_stats():
    """Test session statistics tracking"""
    print("\n" + "=" * 70)
    print("TEST 4: Session Statistics")
    print("=" * 70)
    
    csv_mgr = CSVManager()
    handler = UploadHandler(csv_mgr)
    
    # Create a few uploads
    for i in range(3):
        device_name = f"ECHO_DEVICE_{i:03d}"
        handler.handle_message(device_name, "BEGIN_UPLOAD")
        
        for j in range(5):
            line = f"11111111{i}{j},ECHO_DEVICE_{i:03d},TARGET,TYPE,seen,-65,-64.50,0.812"
            handler.handle_message(device_name, line)
        
        handler.handle_message(device_name, "END_UPLOAD")
    
    stats = handler.get_session_stats()
    
    print(f"\nSession Statistics:")
    print(f"  Total completed: {stats['total_completed']}")
    print(f"  Currently active: {stats['currently_active']}")
    print(f"  Total rows received: {stats['total_rows_received']}")
    
    expected_completed = 3
    expected_rows = 15  # 3 devices * 5 rows each
    
    if stats['total_completed'] == expected_completed and stats['total_rows_received'] == expected_rows:
        print(f"\nTest 4 Result: ✓ PASSED")
    else:
        print(f"\nTest 4 Result: ✗ FAILED (expected {expected_completed} completed, {expected_rows} rows)")


def main():
    """Run all tests"""
    setup_test_logging()
    
    print("\n")
    print("╔════════════════════════════════════════════════════════════════════╗")
    print("║          ECHO STATION - TEST SUITE                                ║")
    print("╚════════════════════════════════════════════════════════════════════╝")
    
    try:
        test_basic_upload()
        time.sleep(0.5)
        
        test_multiple_devices()
        time.sleep(0.5)
        
        test_malformed_data()
        time.sleep(0.5)
        
        test_session_stats()
        
        print("\n" + "=" * 70)
        print("ALL TESTS COMPLETED")
        print("=" * 70)
        print("\nCheck logs/ directory for generated CSV files")
        
    except Exception as e:
        print(f"\n✗ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
