from django.conf import settings
from django.db import models, transaction
from django.db.models import F

class Categoria(models.Model):
    nombre = models.CharField(max_length=100)

    class Meta:
        verbose_name_plural = "categorías"

    def __str__(self):
        return self.nombre


class Producto(models.Model):
    categoria = models.ForeignKey(
        Categoria, on_delete=models.CASCADE, related_name="productos"
    )
    nombre = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True)
    precio = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.PositiveIntegerField(default=0)
    imagen = models.ImageField(upload_to="productos/", blank=True)
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nombre


class Pedido(models.Model):
    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente de pago"
        PAGADO = "pagado", "Pagado, por enviar"
        ENVIADO = "enviado", "Enviado"
        EN_TRANSITO = "en_transito", "En tránsito"
        ENTREGADO = "entregado", "Entregado"
        CANCELADO = "cancelado", "Cancelado"

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="pedidos"
    )
    nombre = models.CharField(max_length=200)
    direccion = models.TextField()
    email = models.EmailField(blank=True)
    telefono = models.CharField(max_length=30)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    estado = models.CharField(
        max_length=20, choices=Estado.choices, default=Estado.PENDIENTE
    )
    creado = models.DateTimeField(auto_now_add=True)
    pago_id = models.CharField(max_length=64, blank=True)
    mp_order_id = models.CharField(max_length=64, blank=True)
    numero_guia = models.CharField(max_length=100, blank=True)
    cancelacion_solicitada = models.BooleanField(default=False)

    class Meta:
        ordering = ["-creado"]

    def __str__(self):
        return f"Pedido #{self.pk} - {self.usuario}"

    def cancelar(self):
        """Cancela el pedido y devuelve el stock. No hace nada si ya estaba cancelado."""
        if self.estado == self.Estado.CANCELADO:
            return False
        with transaction.atomic():
            for item in self.items.all():
                if item.producto_id:
                    Producto.objects.filter(pk=item.producto_id).update(
                        stock=F("stock") + item.cantidad
                    )
            self.estado = self.Estado.CANCELADO
            self.save(update_fields=["estado"])
        return True

    @property
    def puede_cancelar(self):
        return self.estado == self.Estado.PENDIENTE

    @property
    def puede_editar_envio(self):
        return self.estado in (self.Estado.PENDIENTE, self.Estado.PAGADO)

    @property
    def puede_solicitar_cancelacion(self):
        return self.estado == self.Estado.PAGADO and not self.cancelacion_solicitada

class PedidoItem(models.Model):
    pedido = models.ForeignKey(
        Pedido, on_delete=models.CASCADE, related_name="items"
    )
    producto = models.ForeignKey(
        Producto, on_delete=models.SET_NULL, null=True, blank=True
    )
    nombre = models.CharField(max_length=200)
    precio = models.DecimalField(max_digits=10, decimal_places=2)
    cantidad = models.PositiveIntegerField()

    def __str__(self):
        return f"{self.cantidad} x {self.nombre}"

    @property
    def subtotal(self):
        return self.precio * self.cantidad