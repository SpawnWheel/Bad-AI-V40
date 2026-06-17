import time
import heapq
import glob
import os
import json
from settings_manager import settings

class CommentaryQueue:
    def __init__(self):
        self.queue = [] # List of tuples: (priority, timestamp, item_id, event_data)
                        # We use a negative priority for heapq to simulate a max-heap (highest priority first)
        self.item_counter = 0 # Tie-breaker for stable sorting
        self.processed_events = set() # To avoid duplicates if log is re-read (though we tail)
        self.current_log_file = None
        self.log_file_handle = None
        self.active = False # Start/Stop flag - Default to False
        self.last_sim_time = 0
        
        # Commentary State
        self.current_event = None
        self.commentary_end_time = 0 # Real World Time
        
        # Batching
        self.batch_buffer = []
        self.batch_last_add_time = 0

    def clear(self):
        self.queue = []
        self.processed_events = set()
        self.item_counter = 0
        self.current_event = None
        self.commentary_end_time = 0
        self.batch_buffer = []
        # We don't reset current_log_file or handle here, just the data

    def get_latest_log_file(self):
        # Find the newest file in Logs/
        list_of_files = glob.glob('Logs/race_events_*.jsonl') 
        if not list_of_files:
            return None
        return max(list_of_files, key=os.path.getctime)

    def tail_log_file(self):
        """
        Reads new lines from the current log file.
        If file changes, switches to new one.
        """
        latest_file = self.get_latest_log_file()
        
        # Detect file rotation
        if latest_file and latest_file != self.current_log_file:
            print(f"Switching to new log file: {latest_file}")
            # Clear queue from previous session to avoid mix-up
            self.clear()
            
            if self.log_file_handle:
                self.log_file_handle.close()
            self.current_log_file = latest_file
            self.log_file_handle = open(self.current_log_file, 'r', encoding='utf-8')
            # If switching, do we read from start? Yes, usually.
            
        if self.log_file_handle:
            lines = self.log_file_handle.readlines()
            if lines:
                print(f"Read {len(lines)} new lines.")
            for line in lines:
                if not line.strip(): continue
                try:
                    event = json.loads(line)
                    self.process_event(event)
                except json.JSONDecodeError:
                    pass
                except Exception as e:
                    print(f"Error processing event: {e}")

    def get_recent_history(self, n=50):
        """
        Reads the last N lines from the current log file to provide context.
        """
        if not self.current_log_file:
            return []
            
        try:
            # We need to read the file again to get history, as we only tail it usually
            # Efficient way for large files: seek to end and read backwards? 
            # Or just read all and take last N (easier for now, file rotates anyway)
            with open(self.current_log_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
                return lines[-n:]
        except Exception as e:
            print(f"Error reading history: {e}")
            return []

    def calculate_priority(self, event):
        """
        Calculates priority score based on Category and Context.
        Returns float score (higher is better).
        """
        category = event.get('category', 'UNKNOWN')
        priorities = settings.get("filter", "priorities")
        base_score = priorities.get(category, 50)
        
        # Position Multiplier (Applies to all events with 'Pxx' in message or explicit place)
        multiplier = 1.0
        place = event.get('place')
        
        # If place not explicit, try to parse from message
        if place is None:
            import re
            message = event.get('message', '')
            match = re.search(r'\bP(\d+)\b', message)
            if match:
                place = int(match.group(1))
        
        if place is not None:
            multipliers = settings.get("filter", "position_multipliers")
            if place == 1:
                multiplier = multipliers.get("p1", 1.5)
            elif place <= 3:
                multiplier = multipliers.get("podium", 1.3)
            elif place <= 10:
                multiplier = multipliers.get("top_10", 1.1)
            else:
                multiplier = multipliers.get("mid_field", 1.0) # Default
                
        # Final Score
        return base_score * multiplier

    def queue_event(self, event):
        """
        Internal helper to actually push to heap.
        """
        score = self.calculate_priority(event)
        category = event.get('category', 'UNKNOWN')
        timeouts = settings.get("filter", "timeouts")
        ttl = timeouts.get(category, 30)
        
        # Expiration uses SYSTEM TIME (Real World Time)
        now_real = time.time()
        expiration_time = now_real + ttl
        sim_time = event.get('sim_time_raw', 0)
        
        event_wrapper = {
            'data': event,
            'score': score,
            'expiration': expiration_time, # System timestamp
            'added_at': now_real,
            'sim_time': sim_time,
            'id': self.item_counter
        }
        
        # --- INTERRUPTION LOGIC ---
        if self.current_event and self.active:
            threshold = settings.get("filter", "interruption_threshold", 10.0)
            
            # If the new event is significantly more important than the current one
            if score > (self.current_event['score'] + threshold):
                print(f"INTERRUPT: {category} (Score {score:.1f}) interrupted {self.current_event['data']['category']} (Score {self.current_event['score']:.1f})")
                
                # Replace current event immediately
                self.current_event = event_wrapper
                
                # Reset Timer (Real Time)
                duration = settings.get("filter", "commentary_duration", 5.0)
                self.commentary_end_time = time.time() + duration
                
                # We do NOT push to queue, it goes straight to air.
                return

        # Special Handling for CLOSEST_BATTLE (Filler)
        # We only want ONE (the latest) in the queue at any time.
        if category == 'CLOSEST_BATTLE':
            # Remove any existing CLOSEST_BATTLE events
            original_len = len(self.queue)
            self.queue = [item for item in self.queue if item[2]['data'].get('category') != 'CLOSEST_BATTLE']
            if len(self.queue) != original_len:
                heapq.heapify(self.queue)
                # print("Replaced existing CLOSEST_BATTLE in queue.")

        # Normal Queue Add
        heapq.heappush(self.queue, (-score, self.item_counter, event_wrapper))
        self.item_counter += 1
        print(f"Queued: {category} - {event.get('message')}")

    def process_event(self, event):
        if not self.active:
            return

        message = event.get('message', '')
        
        # --- FILTERING LOGIC ---
        # Ignore internal system logs from commentary
        if "New log file started" in message: return
        if "Connected to RaceRoom shared memory" in message: return
        if "Phase Update: Counting Down" in message: return

        # --- INTERRUPT LOGIC ---
        if "Green Flag" in event.get('message', ''):
            print("INTERRUPT: Green Flag detected! Taking over commentary.")
            
            # Flush any pending setup batch first (so we don't lose it)
            self.flush_batch()
            
            # Force active
            # Use System Time for expiration/duration
            now_real = time.time()
            self.current_event = {
                'data': event,
                'score': 999.0, # Artificial high score
                'expiration': now_real + 60,
                'sim_time': event.get('sim_time_raw', 0)
            }
            # Set duration (Real Time)
            duration = settings.get("filter", "commentary_duration", 5.0)
            self.commentary_end_time = now_real + duration
            return

        # --- BATCHING LOGIC ---
        category = event.get('category', 'UNKNOWN')
        if category in ['TRACK', 'SESSION', 'SYSTEM', 'LEADERBOARD']:
            # If queue is empty and current event is None (typical start scenario)
            # OR if we are already batching
            if (not self.queue and not self.current_event) or self.batch_buffer:
                self.batch_buffer.append(event)
                # Only set start time if it's the first item
                if len(self.batch_buffer) == 1:
                    self.batch_last_add_time = time.time()
                return

        # If we got here, it's a normal event. 
        self.flush_batch()
        
        self.queue_event(event)

    def flush_batch(self):
        if not self.batch_buffer:
            return
            
        print(f"Flushing batch of {len(self.batch_buffer)} events.")
        
        # Combine messages
        messages = []
        for e in self.batch_buffer:
            msg = e.get('message', '')
            if msg not in messages:
                messages.append(msg)
        
        combined_msg = " | ".join(messages)
        
        combined_event = {
            'timestamp': self.batch_buffer[0]['timestamp'],
            'sim_time_raw': self.batch_buffer[0]['sim_time_raw'],
            'category': 'SESSION_UPDATE',
            'message': f"Session Update: {combined_msg}"
        }
        
        self.batch_buffer = []
        self.queue_event(combined_event)

    def manage_queue(self):
        """
        Manages the queue lifecycle.
        """
        now_real = time.time()
        
        # 0. Check Batch Timeout
        if self.batch_buffer:
            if now_real - self.batch_last_add_time > 1.0: # 1s silence to collect startup messages
                self.flush_batch()
        
        # A. Check Current Event (REAL TIME)
        if self.current_event:
            # If time is up
            if now_real >= self.commentary_end_time:
                print(f"Finished commentary on: {self.current_event['data']['category']}")
                self.current_event = None
                
        # B. Promote New Event if Free
        if not self.current_event and self.queue and self.active:
            # Loop until we find a valid (non-expired) item
            while self.queue:
                item = heapq.heappop(self.queue)
                wrapper = item[2]
                
                # Check Expiration (REAL TIME)
                if wrapper['expiration'] > now_real:
                    self.current_event = wrapper
                    duration = settings.get("filter", "commentary_duration", 5.0)
                    self.commentary_end_time = now_real + duration
                    print(f"Starting commentary on: {wrapper['data']['message']}")
                    break
                else:
                    # Expired while in queue, drop it
                    pass

        # C. Clean Backlog (Expired items)
        new_queue = []
        for item in self.queue:
            wrapper = item[2]
            if wrapper['expiration'] > now_real:
                new_queue.append(item)
        
        if len(new_queue) != len(self.queue):
            heapq.heapify(new_queue)
            self.queue = new_queue

    def get_snapshot(self):
        """
        Returns a sorted list of current queue items for UI display.
        Also returns the current active event.
        """
        now_real = time.time()
        
        # Sort by priority (high to low), then insertion order
        sorted_items = sorted(self.queue, key=lambda x: (x[0], x[1]))
        
        queue_results = []
        for _, _, wrapper in sorted_items:
            queue_results.append({
                'category': wrapper['data'].get('category'),
                'message': wrapper['data'].get('message'),
                'priority': wrapper['score'],
                'expires_in': max(0, wrapper['expiration'] - now_real) # Real Time
            })
            
        active_result = None
        if self.current_event:
            active_result = {
                'category': self.current_event['data'].get('category'),
                'message': self.current_event['data'].get('message'),
                'priority': self.current_event['score'],
                'time_left': max(0, self.commentary_end_time - now_real) # Real Time
            }
            
        return active_result, queue_results

    def update(self):
        """
        Main loop step.
        """
        settings.load() # Refresh settings
        self.tail_log_file()
        self.manage_queue()

# Singleton for app use
commentary_queue = CommentaryQueue()