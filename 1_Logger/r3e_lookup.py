import json
import os

class R3ELookup:
    def __init__(self, json_path=None):
        self.data = None
        self.cars = {}
        self.classes = {}
        
        if json_path is None:
            # Default to the documentation folder relative to this script
            current_dir = os.path.dirname(os.path.abspath(__file__))
            json_path = os.path.join(current_dir, "raceroomdocumentation", "r3e-data.json")
            
        self.load_data(json_path)

    def load_data(self, json_path):
        try:
            if os.path.exists(json_path):
                with open(json_path, 'r', encoding='utf-8') as f:
                    self.data = json.load(f)
                    self.cars = self.data.get("cars", {})
                    self.classes = self.data.get("classes", {})
                print(f"R3E Data loaded successfully from {json_path}")
            else:
                print(f"Warning: R3E Data file not found at {json_path}")
        except Exception as e:
            print(f"Error loading R3E Data: {e}")

    def get_car_name(self, car_id):
        car_id = str(car_id)
        if car_id in self.cars:
            return self.cars[car_id].get("Name", "Unknown Car")
        return "Unknown Car"

    def get_class_name(self, class_id):
        class_id = str(class_id)
        if class_id in self.classes:
            return self.classes[class_id].get("Name", "Unknown Class")
        return "Unknown Class"

    def get_livery_name(self, car_id, livery_id):
        car_id = str(car_id)
        if car_id in self.cars:
            liveries = self.cars[car_id].get("liveries", [])
            for livery in liveries:
                if livery.get("Id") == livery_id:
                    return livery.get("Name", "Unknown Livery")
        return "Unknown Livery"

# Global instance
r3e_lookup = R3ELookup()
