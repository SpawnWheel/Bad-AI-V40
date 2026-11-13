import re

# ------------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------------
INPUT_FILE = "r3e_header.h"
OUTPUT_FILE = "R3E_Memory_Map.txt"

# Standard C type sizes (in bytes)
TYPE_SIZES = {
    'char': 1,
    'int8_t': 1, 'uint8_t': 1,
    'int16_t': 2, 'uint16_t': 2,
    'int32_t': 4, 'uint32_t': 4,
    'int': 4, 'long': 4,
    'float': 4, 'double': 8,
    'bool': 1,
}

# ------------------------------------------------------------------
# PARSER LOGIC
# ------------------------------------------------------------------

def parse_header_map(filename):
    with open(filename, 'r') as f:
        lines = f.readlines()

    # Store definitions
    constants = {}
    struct_defs = {} # { 'struct_name': [ {'name': 'field', 'type': 'type', 'array_size': 1} ] }
    type_aliases = {}
    
    # Regex Patterns
    re_typedef = re.compile(r'typedef\s+(\w+)\s+(\w+);')
    re_enum_val = re.compile(r'\s*(\w+)\s*=\s*(-?\d+)')
    re_struct_start = re.compile(r'typedef\s+struct')
    re_struct_end = re.compile(r'}\s*(\w+);')
    re_field = re.compile(r'\s*(\w+)\s+(\w+)(\[.*?\])?;')

    current_struct = None
    current_fields = []

    for line in lines:
        line = line.split('//')[0].strip() # Strip comments
        if not line: continue

        # 1. Parse Constants (Enums and Defines)
        # We cheat a bit and look for "NAME = VALUE" patterns common in enums
        if '=' in line and ('enum' not in line):
            m = re_enum_val.search(line)
            if m:
                constants[m.group(1)] = int(m.group(2))

        # 2. Parse TypeDefs
        m_td = re_typedef.match(line)
        if m_td:
            base, alias = m_td.groups()
            type_aliases[alias] = base
            # If the base is a known size, register the alias size immediately
            if base in TYPE_SIZES:
                TYPE_SIZES[alias] = TYPE_SIZES[base]
            elif base in type_aliases: # Alias of an alias
                 if type_aliases[base] in TYPE_SIZES:
                     TYPE_SIZES[alias] = TYPE_SIZES[type_aliases[base]]

        # 3. Parse Structs
        if re_struct_start.match(line):
            current_struct = "PENDING"
            current_fields = []
            continue

        if current_struct:
            m_end = re_struct_end.match(line)
            if m_end:
                struct_name = m_end.group(1)
                struct_defs[struct_name] = current_fields
                current_struct = None
                continue
            
            # Parse Field
            m_field = re_field.match(line)
            if m_field:
                f_type, f_name, f_arr = m_field.groups()
                
                # Resolve Array Size
                arr_size = 1
                if f_arr:
                    content = f_arr.strip('[]')
                    if content.isdigit():
                        arr_size = int(content)
                    elif content in constants:
                        arr_size = constants[content]
                    else:
                        arr_size = 1 # Fallback or error

                current_fields.append({
                    'type': f_type,
                    'name': f_name,
                    'count': arr_size
                })

    return struct_defs, type_aliases, constants

def calculate_size(type_name, struct_defs, type_aliases):
    """Recursively calculate the size of a type."""
    # 1. Check Primitives
    if type_name in TYPE_SIZES:
        return TYPE_SIZES[type_name]
    
    # 2. Check Aliases
    if type_name in type_aliases:
        return calculate_size(type_aliases[type_name], struct_defs, type_aliases)
    
    # 3. Check Structs
    if type_name in struct_defs:
        total = 0
        for field in struct_defs[type_name]:
            s = calculate_size(field['type'], struct_defs, type_aliases)
            total += s * field['count']
        return total
    
    # 4. Fallback for Enums (usually int32 in C)
    return 4

def generate_flat_map(root_struct_name, struct_defs, type_aliases):
    """Generates a flat list of absolute offsets."""
    output_lines = []
    
    def traverse(struct_name, base_offset, prefix):
        current_offset = base_offset
        
        if struct_name not in struct_defs:
            return

        for field in struct_defs[struct_name]:
            field_type = field['type']
            field_name = field['name']
            field_count = field['count']
            
            # Resolve size of single element
            element_size = calculate_size(field_type, struct_defs, type_aliases)
            
            # Is this a primitive or a nested struct?
            is_nested = field_type in struct_defs
            
            for i in range(field_count):
                name_str = f"{prefix}.{field_name}" if prefix else field_name
                if field_count > 1:
                    name_str += f"[{i}]"
                
                if is_nested:
                    # Recurse
                    traverse(field_type, current_offset, name_str)
                else:
                    # Print Leaf
                    hex_off = f"0x{current_offset:04X}"
                    dec_off = f"{current_offset:05d}"
                    out_line = f"OFFSET: {hex_off} ({dec_off}) | TYPE: {field_type:<15} | SIZE: {element_size} | {name_str}"
                    output_lines.append(out_line)
                
                current_offset += element_size

    traverse(root_struct_name, 0, "")
    return output_lines

# ------------------------------------------------------------------
# EXECUTION
# ------------------------------------------------------------------

if __name__ == "__main__":
    try:
        structs, aliases, consts = parse_header_map(INPUT_FILE)
        
        # Calculate size for all structs (to fill cache)
        for s in structs:
            calculate_size(s, structs, aliases)

        # Generate Map for r3e_shared (the root struct)
        map_lines = generate_flat_map('r3e_shared', structs, aliases)
        
        with open(OUTPUT_FILE, 'w') as f:
            f.write(f"R3E SHARED MEMORY MAP\n")
            f.write(f"Generated from {INPUT_FILE}\n")
            f.write("="*80 + "\n")
            f.write("\n".join(map_lines))
            
        print(f"Success! Map generated at: {OUTPUT_FILE}")
        
    except FileNotFoundError:
        print(f"Error: Could not find '{INPUT_FILE}'. Please create it with the C header content.")
    except Exception as e:
        print(f"An error occurred: {e}")