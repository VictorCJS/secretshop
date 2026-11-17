from django.urls import path
from . import views

urlpatterns = [
    path("", views.lista_productos, name="lista_productos"),
    path("producto/<int:pk>/", views.detalle_producto, name="detalle_producto"),
    path("registro/", views.registro, name="registro"),
    path("carrito/", views.ver_carrito, name="ver_carrito"),
    path("carrito/agregar/<int:pk>/", views.agregar_al_carrito, name="agregar_al_carrito"),
    path("carrito/quitar/<int:pk>/", views.quitar_del_carrito, name="quitar_del_carrito"),
    path("checkout/", views.checkout, name="checkout"),
    path("pedido/<int:pk>/confirmado/", views.pedido_confirmado, name="pedido_confirmado"),
    path("pedido/<int:pk>/pagar/", views.pagar_pedido, name="pagar_pedido"),
    path("pago/retorno/", views.pago_retorno, name="pago_retorno"),
    path("pago/webhook/", views.mp_webhook, name="mp_webhook"),
    path("mis-pedidos/", views.mis_pedidos, name="mis_pedidos"),
]