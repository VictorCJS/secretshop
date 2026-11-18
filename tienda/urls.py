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

    # Panel de administración
    path("administrar/", views.panel_inicio, name="panel_inicio"),
    path("administrar/pedidos/", views.panel_pedidos, name="panel_pedidos"),
    path("administrar/pedidos/<int:pk>/estado/", views.panel_cambiar_estado, name="panel_cambiar_estado"),
    path("administrar/productos/", views.panel_productos, name="panel_productos"),
    path("administrar/productos/nuevo/", views.panel_producto_form, name="panel_producto_nuevo"),
    path("administrar/productos/<int:pk>/editar/", views.panel_producto_form, name="panel_producto_editar"),
    path("administrar/productos/<int:pk>/activar/", views.panel_producto_activar, name="panel_producto_activar"),
    path("administrar/categorias/", views.panel_categorias, name="panel_categorias"),
    path("pedido/<int:pk>/cancelar/", views.cancelar_mi_pedido, name="cancelar_mi_pedido"),
    path("pedido/<int:pk>/solicitar-cancelacion/", views.solicitar_cancelacion, name="solicitar_cancelacion"),
    path("pedido/<int:pk>/editar-envio/", views.editar_envio, name="editar_envio"),
    path("pedido/<int:pk>/repetir/", views.repetir_pedido, name="repetir_pedido"),
]