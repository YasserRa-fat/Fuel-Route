import logging

from django.http import HttpResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import serializers, status

from .services import calculate_route

logger = logging.getLogger(__name__)


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=200)
    finish = serializers.CharField(max_length=200)
    max_range_miles = serializers.FloatField(required=False, default=500, min_value=50, max_value=2000)
    mpg = serializers.FloatField(required=False, default=10, min_value=1, max_value=200)


class FuelRouteView(APIView):
    def post(self, request):
        serializer = RouteRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {'error': 'Invalid request', 'details': serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        data = serializer.validated_data
        try:
            result = calculate_route(
                start=data['start'],
                finish=data['finish'],
                max_range=data.get('max_range_miles'),
                mpg=data.get('mpg'),
            )
            return Response(result, status=status.HTTP_200_OK)
        except ValueError as exc:
            logger.warning("Route calculation failed: %s", exc)
            return Response({'error': str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        except Exception:
            logger.exception("Unexpected error in FuelRouteView")
            return Response({'error': 'Internal server error.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def get(self, request):
        return Response({
            'endpoints': {
                'POST /api/route/': 'JSON route data and fuel stops',
                'GET /api/health/': 'Health check',
            },
            'parameters': {
                'start': 'string — Starting location in the USA (required)',
                'finish': 'string — Destination in the USA (required)',
                'max_range_miles': 'float — Vehicle range in miles (optional, default 500)',
                'mpg': 'float — Miles per gallon (optional, default 10)',
            },
            'example_request': {
                'start': 'Chicago, IL',
                'finish': 'Los Angeles, CA',
                'max_range_miles': 500,
                'mpg': 10,
            },
        })




class HealthView(APIView):
    def get(self, request):
        return Response({'status': 'ok'})