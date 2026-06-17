import json
import os

def extract_raceroom_data(input_file, output_file):
    if not os.path.exists(input_file):
        print(f"Error: {input_file} not found.")
        return

    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if 'TrackLandmarksData' not in data:
        print("Error: 'TrackLandmarksData' key not found in JSON.")
        return

    # Filter for entries that have 'raceroomLayoutId'
    raceroom_data = [
        entry for entry in data['TrackLandmarksData'] 
        if 'raceroomLayoutId' in entry
    ]

    output_dict = {
        "TrackLandmarksData": raceroom_data
    }

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(output_dict, f, indent=4)

    print(f"Successfully extracted {len(raceroom_data)} Raceroom entries to {output_file}")

if __name__ == "__main__":
    extract_raceroom_data('trackLandmarksData.json', 'raceroomTrackLandmarksData.json')
