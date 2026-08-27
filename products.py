import json
import os

# Path to the JSON data file
DATA_FILE = os.path.join('data', 'products.json')

def load_all_products():
    """Reads and returns all products from the JSON database file."""
    if not os.path.exists(DATA_FILE):
        return []
    with open(DATA_FILE, 'r') as file:
        return json.load(file)

def filter_products(category="all", search_query=""):
    """
    Filters products based on category and search keyword.
    """
    products = load_all_products()
    filtered_list = []

    for product in products:
        # Check category match
        matches_category = (category == "all" or product['category'].lower() == category.lower())
        
        # Check search keyword match (in product name)
        matches_search = (search_query.lower() in product['name'].lower())

        if matches_category and matches_search:
            filtered_list.append(product)

    return filtered_list

# Test run functions directly in terminal
if __name__ == "__main__":
    print("--- ALL PRODUCTS ---")
    print(load_all_products())

    print("\n--- FILTERED (Category: Books) ---")
    print(filter_products(category="books"))

    print("\n--- SEARCH (Query: 'Calculator') ---")
    print(filter_products(search_query="Calculator"))