from django.apps import AppConfig

class FuelRouteConfig(AppConfig):
    name = 'fuel_route'

    def ready(self):
        from .services import _load_stations
        _load_stations()