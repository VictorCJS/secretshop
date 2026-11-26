from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import F
from decimal import Decimal
def validar_enlace(valor):
    """Solo se permiten direcciones del propio sitio (/...) o http(s)://..."""
    if not valor:
        return
    propio = valor.startswith("/") and not valor.startswith("//")
    externo = valor.startswith(("http://", "https://"))
    if not (propio or externo):
        raise ValidationError("Usa una dirección que empiece con / (de tu sitio) o con https://")

class Categoria(models.Model):
    nombre = models.CharField(max_length=100)
    padre = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="subcategorias"
    )

    class Meta:
        verbose_name_plural = "categorías"
        ordering = ["nombre"]

    def __str__(self):
        return f"{self.padre.nombre} › {self.nombre}" if self.padre_id else self.nombre

    def clean(self):
        if self.padre_id:
            if self.padre_id == self.pk:
                raise ValidationError("Una categoría no puede pertenecer a sí misma.")
            if self.padre.padre_id:
                raise ValidationError("Solo se permiten dos niveles: categoría y subcategoría.")
            if self.pk and self.subcategorias.exists():
                raise ValidationError("Esta categoría ya tiene subcategorías y no puede ser subcategoría.")
        repetida = Categoria.objects.filter(padre=self.padre, nombre__iexact=self.nombre)
        if self.pk:
            repetida = repetida.exclude(pk=self.pk)
        if repetida.exists():
            raise ValidationError("Ya existe una categoría con ese nombre en este nivel.")

class Franquicia(models.Model):
    nombre = models.CharField(max_length=100)

    class Meta:
        verbose_name_plural = "franquicias"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre

    def clean(self):
        self.nombre = " ".join(self.nombre.split())
        repetida = Franquicia.objects.filter(nombre__iexact=self.nombre)
        if self.pk:
            repetida = repetida.exclude(pk=self.pk)
        if repetida.exists():
            raise ValidationError("Ya existe una franquicia con ese nombre.")


class TipoProducto(models.Model):
    nombre = models.CharField(max_length=100)
    class Submenu(models.TextChoices):
        FRANQUICIAS = "franquicias", "Franquicias"
        LINEAS = "lineas", "Líneas y marcas"
        AMBAS = "ambas", "Líneas y franquicias"

    submenu = models.CharField(
        max_length=20, choices=Submenu.choices, default=Submenu.FRANQUICIAS
    )
    class Meta:
        verbose_name = "tipo de producto"
        verbose_name_plural = "tipos de producto"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre

    def clean(self):
        self.nombre = " ".join(self.nombre.split())
        repetida = TipoProducto.objects.filter(nombre__iexact=self.nombre)
        if self.pk:
            repetida = repetida.exclude(pk=self.pk)
        if repetida.exists():
            raise ValidationError("Ya existe un tipo de producto con ese nombre.")

class Linea(models.Model):
    """Línea o formato del producto: Nendoroid, Pop Up Parade, Booster Box…"""
    nombre = models.CharField(max_length=100)

    class Meta:
        verbose_name = "línea"
        verbose_name_plural = "líneas"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre

    def clean(self):
        self.nombre = " ".join(self.nombre.split())
        repetida = Linea.objects.filter(nombre__iexact=self.nombre)
        if self.pk:
            repetida = repetida.exclude(pk=self.pk)
        if repetida.exists():
            raise ValidationError("Ya existe una línea con ese nombre.")


class Condicion(models.Model):
    """Estado del producto, definido para cada tipo (Nuevo/Usado, Gradeado/Raw/Sellado/Dañado…)."""
    tipo = models.ForeignKey(
        TipoProducto, on_delete=models.CASCADE, related_name="condiciones"
    )
    nombre = models.CharField(max_length=60)

    class Meta:
        verbose_name = "estado"
        verbose_name_plural = "estados"
        ordering = ["tipo__nombre", "nombre"]
        constraints = [
            models.UniqueConstraint(fields=["tipo", "nombre"], name="condicion_unica_por_tipo"),
        ]

    def __str__(self):
        return f"{self.tipo} · {self.nombre}"

    def clean(self):
        self.nombre = " ".join(self.nombre.split())
        if not self.tipo_id:
            return
        repetida = Condicion.objects.filter(tipo_id=self.tipo_id, nombre__iexact=self.nombre)
        if self.pk:
            repetida = repetida.exclude(pk=self.pk)
        if repetida.exists():
            raise ValidationError("Ese tipo ya tiene un estado con ese nombre.")

class Producto(models.Model):
    categoria = models.ForeignKey(
        Categoria, null=True, blank=True, on_delete=models.PROTECT, related_name="productos"
    )  # antigua: se eliminará en la limpieza
    franquicia = models.ForeignKey(
        Franquicia, null=True, blank=True, on_delete=models.PROTECT, related_name="productos"
    )
    tipo = models.ForeignKey(
        TipoProducto, null=True, blank=True, on_delete=models.PROTECT, related_name="productos"
    )
    linea = models.ForeignKey(
        Linea, null=True, blank=True, on_delete=models.PROTECT, related_name="productos"
    )
    condicion = models.ForeignKey(
        Condicion, null=True, blank=True, on_delete=models.PROTECT, related_name="productos"
    )
    detalle_condicion = models.CharField(max_length=120, blank=True)
    nombre = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True)
    precio = models.DecimalField(max_digits=10, decimal_places=2)
    stock = models.PositiveIntegerField(default=0)
    imagen = models.ImageField(upload_to="productos/", blank=True)
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.nombre
class ProductoImagen(models.Model):
    producto = models.ForeignKey(
        Producto, on_delete=models.CASCADE, related_name="imagenes"
    )
    imagen = models.ImageField(upload_to="productos/")
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "foto adicional"
        verbose_name_plural = "fotos adicionales"
        ordering = ["id"]
        
class Banner(models.Model):
    titulo = models.CharField(max_length=120, blank=True)
    subtitulo = models.CharField(max_length=200, blank=True)
    imagen = models.ImageField(upload_to="banners/")
    enlace = models.CharField(
        max_length=300, blank=True, validators=[validar_enlace],
        help_text="Opcional. Ejemplo: /?categoria=3 o https://...",
    )
    orden = models.PositiveSmallIntegerField(
        default=0, help_text="Los números menores salen primero."
    )
    activo = models.BooleanField(default=True)

    class Meta:
        ordering = ["orden", "-id"]

    def __str__(self):
        return self.titulo or f"Banner #{self.pk}"

class Pedido(models.Model):
    class Canal(models.TextChoices):
        ONLINE = "online", "Online"
        FISICO = "fisico", "Físico"

    canal = models.CharField(max_length=10, choices=Canal.choices, default=Canal.ONLINE)
    metodo_pago = models.CharField(max_length=20, blank=True)
    class Estado(models.TextChoices):
        PENDIENTE = "pendiente", "Pendiente de pago"
        PAGADO = "pagado", "Pagado, por enviar"
        ENVIADO = "enviado", "Enviado"
        EN_TRANSITO = "en_transito", "En tránsito"
        ENTREGADO = "entregado", "Entregado"
        CANCELADO = "cancelado", "Cancelado"
        POR_VERIFICAR = "por_verificar", "Transferencia por verificar"

    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.PROTECT, related_name="pedidos",
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
    referencia_pago = models.CharField(max_length=100, blank=True)
    monto_recibido = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

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
        return self.estado in (
            self.Estado.PENDIENTE, self.Estado.POR_VERIFICAR, self.Estado.PAGADO
        )

    @property
    def puede_solicitar_cancelacion(self):
        return self.estado == self.Estado.PAGADO and not self.cancelacion_solicitada
    @property
    def cambio(self):
        if self.monto_recibido is None:
            return None
        return self.monto_recibido - self.total

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
def registrar_venta_fisica(
    lineas, metodo_pago="", usuario=None, cliente="",
    monto_recibido=None, referencia="",
):
    """lineas = [(producto_id, cantidad, precio_o_None), ...]

    Si el precio es None se usa el precio actual del producto.
    Si algo falla, se lanza ValidationError y no se guarda nada.
    """
    if not lineas:
        raise ValidationError("No agregaste ningún producto.")

    with transaction.atomic():
        pedido = Pedido.objects.create(
            usuario=usuario,
            canal=Pedido.Canal.FISICO,
            metodo_pago=metodo_pago,
            nombre=cliente or "Venta física",
            direccion="Venta en mostrador",
            total=0,
            estado=Pedido.Estado.ENTREGADO,
            referencia_pago=referencia if metodo_pago == "transferencia" else "",
        )
        total = Decimal("0")

        for producto_id, cantidad, precio in lineas:
            if cantidad < 1:
                raise ValidationError("La cantidad debe ser de al menos 1.")
            try:
                p = Producto.objects.select_for_update().get(pk=producto_id)
            except Producto.DoesNotExist:
                raise ValidationError("Uno de los productos ya no existe.")
            if p.stock < cantidad:
                raise ValidationError(f"Solo quedan {p.stock} de {p.nombre}.")

            precio = p.precio if precio is None else Decimal(str(precio))
            if precio < 0:
                raise ValidationError("El precio no puede ser negativo.")

            p.stock -= cantidad
            p.save(update_fields=["stock"])
            PedidoItem.objects.create(
                pedido=pedido, producto=p, nombre=p.nombre,
                precio=precio, cantidad=cantidad,
            )
            total += precio * cantidad

        if metodo_pago == "efectivo":
            if monto_recibido is not None and monto_recibido < total:
                raise ValidationError("El monto recibido es menor al total.")
            pedido.monto_recibido = monto_recibido if monto_recibido is not None else total

        pedido.total = total
        pedido.save(update_fields=["total", "monto_recibido"])
    return pedido