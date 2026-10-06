"""Coarse car title evidence and diverse discovery terms, not VIN identities."""
import re

# Model families are search/evidence terms. They do not establish engine,
# generation, exact variant, registration year, condition or repair fitment.
FAMILIES = {
    'Fiat': ('500X', '500L', '500', 'Panda', 'Punto', 'Tipo', 'Bravo'),
    'Volkswagen': ('Golf', 'Polo', 'Passat', 'Tiguan', 'T-Roc', 'Up'),
    'Audi': ('A1', 'A3', 'A4', 'A5', 'A6', 'Q2', 'Q3', 'Q5'),
    'BMW': ('Serie 1', 'Serie 2', 'Serie 3', 'Serie 5', 'X1', 'X3'),
    'Mercedes-Benz': ('Classe A', 'Classe B', 'Classe C', 'GLA'),
    'Renault': ('Clio', 'Captur', 'Megane', 'Scenic'),
    'Peugeot': ('208', '308', '2008', '3008'),
    'Toyota': ('Yaris', 'Aygo', 'Auris', 'Corolla', 'RAV4'),
    'Ford': ('Fiesta', 'Focus', 'Kuga'),
    'Opel': ('Corsa', 'Astra', 'Mokka'),
    'Citroen': ('C3', 'C4'),
    'Dacia': ('Sandero', 'Duster'),
    'Hyundai': ('i10', 'i20', 'i30', 'Tucson'),
    'Kia': ('Picanto', 'Rio', 'Sportage'),
    'Nissan': ('Micra', 'Juke', 'Qashqai'),
    'Honda': ('Jazz', 'Civic', 'CR-V'),
    'Seat': ('Ibiza', 'Leon'),
    'Skoda': ('Fabia', 'Octavia'),
    'Alfa Romeo': ('Giulietta', 'MiTo'),
    'Mini': ('Cooper', 'Countryman'),
    'Smart': ('Fortwo', 'Forfour'),
}


def title_identity(title):
    if not isinstance(title, str):
        return None
    cleaned = re.sub(r'\s+', ' ', title.replace('+', ' ')).strip()
    match = re.fullmatch(r'(19\d{2}|20\d{2})\s+(.+)', cleaned)
    if not match:
        return None
    rest = match.group(2)
    for make, models in FAMILIES.items():
        make_pattern = re.escape(make)
        if make == 'Mercedes-Benz':
            make_pattern = r'Mercedes(?:[- ]Benz)?'
        elif make == 'Citroen':
            make_pattern = r'Citro[eë]n'
        found = re.match(make_pattern + r'\s+(.*)', rest, re.IGNORECASE)
        if not found:
            continue
        body = found.group(1)
        body = re.sub(r'^' + make_pattern + r'\s+', '', body, flags=re.IGNORECASE)
        if make == 'BMW':
            body = re.sub(r'^BMW\s+', '', body, flags=re.IGNORECASE)
            series = re.match(r'(?:SERIE\s+([1-8])|([1-8])\d{2}[a-z]*)(?:\b|\s)', body, re.IGNORECASE)
            if series:
                return dict(make=make, model='Serie ' + (series.group(1) or series.group(2)),
                            model_year_from_title=int(match.group(1)))
        if make == 'Mercedes-Benz':
            series = re.match(r'(?:Classe\s+)?([ABCES])(?:\s+\d{3}|\b)', body, re.IGNORECASE)
            if series:
                return dict(make=make, model='Classe ' + series.group(1).upper(),
                            model_year_from_title=int(match.group(1)))
        for model in models:
            if re.match(re.escape(model) + r'\b', body, re.IGNORECASE):
                return dict(make=make, model=model, model_year_from_title=int(match.group(1)))
    return None
