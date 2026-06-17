import os
import sys
from google import genai
from google.genai import types

def run_ai_step():
    # 1. Read API Key
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        try:
            with open('API_Key.txt', 'r', encoding='utf-8') as f:
                api_key = f.read().strip()
        except FileNotFoundError:
            print("Error: API_Key.txt not found and GEMINI_API_KEY environment variable not set.")
            return

    # 2. Get Filename
    if len(sys.argv) > 1:
        data_filename = sys.argv[1]
        print(f"Loading file: {data_filename}")
    else:
        data_filename = input("Enter the input filename: ").strip()

    if not os.path.exists(data_filename):
        print(f"Error: {data_filename} not found.")
        return

    # 3. Read The Prompt
    prompt_file = os.environ.get("GEMINI_PROMPT_FILE", "default.txt")
    try:
        with open(prompt_file, 'r', encoding='utf-8') as f:
            prompt_text = f.read()
    except FileNotFoundError:
        print(f"Error: Prompt file '{prompt_file}' not found.")
        return

    # 4. Read the Data File
    try:
        with open(data_filename, 'r', encoding='utf-8') as f:
            data_content = f.read()
    except Exception as e:
        print(f"Error reading data file: {e}")
        return

    # 4b. Optional: Read Project Specific Notes (project_notes.txt)
    project_path = os.environ.get("R3E_PROJECT_PATH")
    project_notes_path = None
    
    if project_path:
        possible_path = os.path.join(project_path, "project_notes.txt")
        if os.path.exists(possible_path):
            project_notes_path = possible_path

    if not project_notes_path:
        data_dir = os.path.dirname(os.path.abspath(data_filename))
        possible_path = os.path.join(data_dir, "project_notes.txt")
        if os.path.exists(possible_path):
             project_notes_path = possible_path

    extra_context = ""
    if project_notes_path and os.path.exists(project_notes_path):
        try:
            with open(project_notes_path, 'r', encoding='utf-8') as f:
                extra_context = f"\n\nProject Specific Instructions:\n{f.read().strip()}"
            print(f"Loaded project context from: {project_notes_path}")
        except Exception as e:
            print(f"Found project_notes.txt but failed to read: {e}")

    # 5. Construct the Input for the Model
    full_prompt = f"{prompt_text}{extra_context}\n\n{data_content}"

    # 6. Call the Model using the google-genai SDK
    client = genai.Client(api_key=api_key)
    model_name = os.environ.get('GEMINI_MODEL_NAME', 'gemini-3.1-pro-preview')
    
    print(f"Using model: {model_name} (Thinking dynamic based on model)")
    print(f"Using prompt: {os.path.basename(prompt_file)}")
    print("Generating...\n")

    try:
        config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(
                include_thoughts=True, 
                thinking_level="HIGH"
            )
        ) if "gemini-3" in model_name.lower() else None

        response_stream = client.models.generate_content_stream(
            model=model_name,
            contents=full_prompt,
            config=config
        )
        
        full_content = []
        
        for chunk in response_stream:
            if not chunk.candidates:
                continue
            for part in chunk.candidates[0].content.parts:
                if part.thought:
                    print(f"\033[90m{part.text}\033[0m", end="", flush=True)
                elif part.text:
                    print(part.text, end="", flush=True)
                    full_content.append(part.text)
        
        print("\n")
        final_text = "".join(full_content)
        
        # 7. Save the Output
        base_name = os.path.splitext(os.path.basename(data_filename))[0]
        prefix = os.environ.get("GEMINI_OUTPUT_PREFIX", "")
        suffix = os.environ.get("GEMINI_OUTPUT_SUFFIX", ".txt")
        
        # handle overlapping prefixes
        if prefix and base_name.startswith(prefix):
            out_name = f"{base_name}{suffix}"
        else:
            out_name = f"{prefix}{base_name}{suffix}"
        
        if project_path:
            output_filename = os.path.join(project_path, out_name)
        else:
            output_filename = out_name

        with open(output_filename, 'w', encoding='utf-8') as f:
            f.write(final_text)
            
        print(f"Success! Saved to: {output_filename}")

    except Exception as e:
        print(f"An error occurred during generation: {e}")

if __name__ == "__main__":
    run_ai_step()