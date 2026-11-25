from django.contrib import admin

from .models import (
    Banner, Categoria, Franquicia, Pedido, PedidoItem, Producto, TipoProducto,
)


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "padre")
    search_fields = ("nombre",)


@admin.register(Franquicia)
class FranquiciaAdmin(admin.ModelAdmin):
    search_fields = ("nombre",)


@admin.register(TipoProducto)
class TipoProductoAdmin(admin.ModelAdmin):
    list_display = ("nombre", "submenu")
    list_editable = ("submenu",)
    search_fields = ("nombre",)


@admin.register(Banner)
class BannerAdmin(admin.ModelAdmin):
    list_display = ("__str__", "orden", "activo")
    list_editable = ("orden", "activo")


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    exclude = ("categoria",)
    list_display = ("nombre", "franquicia", "tipo", "precio", "stock", "activo")
    list_filter = ("tipo", "franquicia", "activo")
    search_fields = ("nombre",)


class PedidoItemInline(admin.TabularInline):
    model = PedidoItem
    extra = 0
    readonly_fields = ("producto", "nombre", "precio", "cantidad")


@admin.register(Pedido)
class PedidoAdmin(admin.ModelAdmin):
    list_display = (
        "id", "usuario", "nombre", "total", "estado",
        "cancelacion_solicitada", "numero_guia", "creado",
    )
    list_editable = ("numero_guia",)
    list_filter = ("estado", "cancelacion_solicitada", "creado")
    search_fields = ("nombre", "usuario__username", "numero_guia")
    date_hierarchy = "creado"
    inlines = [PedidoItemInline]
    actions = ["marcar_enviado", "marcar_en_transito", "marcar_entregado", "cancelar_pedidos"]

    def save_model(self, request, obj, form, change):
        # Si se cambia el estado a "Cancelado" desde el formulario, también se devuelve el stock.
        cancelando = (
            change
            and "estado" in form.changed_data
            and obj.estado == Pedido.Estado.CANCELADO
        )
        if cancelando:
            obj.estado = form.initial["estado"]
        super().save_model(request, obj, form, change)
        if cancelando:
            obj.cancelar()

    @admin.action(description="Marcar como enviados")
    def marcar_enviado(self, request, queryset):
        n = queryset.filter(estado=Pedido.Estado.PAGADO).update(estado=Pedido.Estado.ENVIADO)
        self.message_user(request, f"{n} pedido(s) marcados como enviados.")

    @admin.action(description="Marcar como en tránsito")
    def marcar_en_transito(self, request, queryset):
        n = queryset.filter(estado=Pedido.Estado.ENVIADO).update(estado=Pedido.Estado.EN_TRANSITO)
        self.message_user(request, f"{n} pedido(s) marcados como en tránsito.")

    @admin.action(description="Marcar como entregados")
    def marcar_entregado(self, request, queryset):
        n = queryset.filter(
            estado__in=[Pedido.Estado.ENVIADO, Pedido.Estado.EN_TRANSITO]
        ).update(estado=Pedido.Estado.ENTREGADO)
        self.message_user(request, f"{n} pedido(s) marcados como entregados.")

    @admin.action(description="Cancelar pedidos (devuelve el stock)")
    def cancelar_pedidos(self, request, queryset):
        n = sum(1 for pedido in queryset if pedido.cancelar())
        self.message_user(request, f"{n} pedido(s) cancelados y con stock devuelto.")