import os

tree = {
    "rental-platform": {
        "backend": {
            "rental_platform": ["__init__.py", "settings.py", "urls.py", "wsgi.py"],
            "accounts": ["__init__.py", "models.py", "serializers.py", "views.py", "urls.py", "migrations/"],
            "rentals": ["__init__.py", "models.py", "serializers.py", "views.py", "urls.py", "migrations/"],
            "reviews": ["__init__.py", "models.py", "serializers.py", "views.py", "urls.py", "migrations/"],
            "media": [],
            "files": ["manage.py", "requirements.txt"]
        },
        "frontend": {
            "src": ["main.jsx", "App.jsx", "components/", "pages/", "services/", "utils/", "styles/"],
            "public": [],
            "files": ["package.json", "vite.config.js", "index.html"]
        },
        "docs": {
            "wireframes": [],
            "diagrams": [],
            "files": ["api-documentation.md", "demo-presentation.md"]
        },
        "files": [".gitignore", "README.md", "docker-compose.yml"]
    }
}

def create_tree(base, structure):
    for key, value in structure.items():
        if key == "files":
            for file in value:
                open(os.path.join(base, file), "a").close()
        else:
            path = os.path.join(base, key)
            os.makedirs(path, exist_ok=True)
            if isinstance(value, dict):
                create_tree(path, value)
            elif isinstance(value, list):
                for item in value:
                    if item.endswith("/"):
                        os.makedirs(os.path.join(path, item), exist_ok=True)
                    else:
                        open(os.path.join(path, item), "a").close()

create_tree(".", tree)
