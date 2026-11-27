from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.utils import timezone
from django.db import models, transaction
from django.db.models import F
from decimal import Decimal
import unicodedata
import secrets

ALFABETO_CODIGO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def generar_codigo_pedido():
    return "".join(secrets.choice(ALFABETO_CODIGO) for _ in range(8))

def clave_normalizada(texto):
    """Para comparar nombres sin importar mayúsculas, acentos ni espacios extra."""
    texto = unicodedata.normalize("NFD", " ".join((texto or "").split()).lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


NOMBRES_ENTIDADES = [
    "Aguascalientes", "Baja California", "Baja California Sur", "Campeche",
    "Chiapas", "Chihuahua", "Ciudad de México", "Coahuila", "Colima", "Durango",
    "Estado de México", "Guanajuato", "Guerrero", "Hidalgo", "Jalisco", "Michoacán",
    "Morelos", "Nayarit", "Nuevo León", "Oaxaca", "Puebla", "Querétaro",
    "Quintana Roo", "San Luis Potosí", "Sinaloa", "Sonora", "Tabasco", "Tamaulipas",
    "Tlaxcala", "Veracruz", "Yucatán", "Zacatecas",
]
ENTIDADES = [(n, n) for n in NOMBRES_ENTIDADES]


def armar_direccion(calle, colonia, codigo_postal, ciudad, entidad, referencia=""):
    lineas = [calle, f"Col. {colonia}, CP {codigo_postal}", f"{ciudad}, {entidad}"]
    if referencia:
        lineas.append(f"Ref.: {referencia}")
    return "\n".join(lineas)
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
        for otra in Franquicia.objects.exclude(pk=self.pk):
            if clave_normalizada(otra.nombre) == clave_normalizada(self.nombre):
                raise ValidationError(f"Ya existe una franquicia llamada «{otra.nombre}».")


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
        for otra in TipoProducto.objects.exclude(pk=self.pk):
            if clave_normalizada(otra.nombre) == clave_normalizada(self.nombre):
                raise ValidationError(f"Ya existe un tipo llamado «{otra.nombre}».")

class Linea(models.Model):
    """Línea o formato del producto: Nendoroid, Pop Up Parade, 30 Aniversario…"""
    nombre = models.CharField(max_length=100)
    tipo = models.ForeignKey(
        "TipoProducto",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="lineas",
        help_text="Tipo de producto al que pertenece esta línea (Figuras, TCG…).",
    )
    franquicia = models.ForeignKey(
        "Franquicia",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="lineas",
        help_text="Déjalo vacío si la línea abarca varias franquicias (ej. Nendoroid).",
    )

    class Meta:
        verbose_name = "línea"
        verbose_name_plural = "líneas"
        ordering = ["nombre"]

    def __str__(self):
        return self.nombre

    def clean(self):
        self.nombre = " ".join(self.nombre.split())
        clave = clave_normalizada(self.nombre)
        for nombre in Linea.objects.exclude(pk=self.pk).values_list("nombre", flat=True):
            if clave_normalizada(nombre) == clave:
                raise ValidationError(f"Ya existe una línea llamada «{nombre}».")

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
        for otra in Condicion.objects.filter(tipo_id=self.tipo_id).exclude(pk=self.pk):
            if clave_normalizada(otra.nombre) == clave_normalizada(self.nombre):
                raise ValidationError(f"«{self.tipo}» ya tiene un estado llamado «{otra.nombre}».")

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
    detalle_condicion = models.CharField(
        max_length=120, blank=True,
        help_text="Para gradeadas: calificadora y nota (ej. PSA 10). Para usadas: estado (ej. ligero desgaste).",
    )
    class Presentacion(models.TextChoices):
        CARTA = "carta", "Carta suelta"
        BOOSTER = "booster", "Sobre"
        CAJA = "caja", "Caja / Booster Box"
        ETB = "etb", "Elite Trainer Box"
        OTRO = "otro", "Otro sellado"

    presentacion = models.CharField(
        max_length=10, choices=Presentacion.choices, blank=True,
        help_text="Solo para TCG.",
    )
    nombre = models.CharField(max_length=200)
    descripcion = models.TextField(blank=True)
    precio = models.DecimalField(max_digits=10, decimal_places=2)
    precio_oferta = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True,validators=[MinValueValidator(Decimal("0.01"))],)
    oferta_hasta = models.DateField(null=True, blank=True)
    stock = models.PositiveIntegerField(default=0)
    imagen = models.ImageField(upload_to="productos/", blank=True)
    activo = models.BooleanField(default=True)
    creado = models.DateTimeField(auto_now_add=True)


    def __str__(self):
        return self.nombre
    @property
    def en_oferta(self):
        if self.precio_oferta is None or self.precio_oferta >= self.precio:
            return False
        return self.oferta_hasta is None or timezone.localdate() <= self.oferta_hasta

    @property
    def precio_final(self):
        return self.precio_oferta if self.en_oferta else self.precio

    @property
    def descuento_porcentaje(self):
        if not self.en_oferta or self.precio <= 0:
            return 0
        return int(round((1 - self.precio_oferta / self.precio) * 100))
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
    codigo_postal = models.CharField(max_length=5, blank=True)
    entidad = models.CharField(max_length=60, choices=ENTIDADES, blank=True)
    ciudad = models.CharField(max_length=100, blank=True)
    colonia = models.CharField(max_length=120, blank=True)
    calle = models.CharField(max_length=200, blank=True)
    referencia = models.CharField(max_length=250, blank=True)
    email = models.EmailField(blank=True)
    telefono = models.CharField(max_length=30)
    total = models.DecimalField(max_digits=10, decimal_places=2)
    estado = models.CharField(max_length=20, choices=Estado.choices, default=Estado.PENDIENTE)
    creado = models.DateTimeField(auto_now_add=True)
    pago_id = models.CharField(max_length=64, blank=True)
    mp_order_id = models.CharField(max_length=64, blank=True)
    numero_guia = models.CharField(max_length=100, blank=True)
    cancelacion_solicitada = models.BooleanField(default=False)
    referencia_pago = models.CharField(max_length=100, blank=True)
    monto_recibido = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    codigo = models.CharField(max_length=8, unique=True, null=True, blank=True, editable=False)


    class Meta:
        ordering = ["-creado"]

    def __str__(self):
        return f"Pedido #{self.pk} - {self.usuario or self.nombre}"
    def save(self, *args, **kwargs):
        if not self.codigo:
            codigo = generar_codigo_pedido()
            while Pedido.objects.filter(codigo=codigo).exists():
                codigo = generar_codigo_pedido()
            self.codigo = codigo
        super().save(*args, **kwargs)

    @property
    def codigo_legible(self):
        return f"{self.codigo[:4]}-{self.codigo[4:]}" if self.codigo else ""

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

            precio = p.precio_final if precio is None else Decimal(str(precio))
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
class Direccion(models.Model):
    usuario = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="direcciones"
    )
    alias = models.CharField(max_length=60, blank=True)  # Casa, Trabajo…
    nombre = models.CharField(max_length=200)
    telefono = models.CharField(max_length=30)
    codigo_postal = models.CharField(max_length=5)
    entidad = models.CharField(max_length=60, choices=ENTIDADES)
    ciudad = models.CharField(max_length=100)
    colonia = models.CharField(max_length=120)
    calle = models.CharField(max_length=200)
    referencia = models.CharField(max_length=250, blank=True)
    principal = models.BooleanField(default=False)
    creado = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "dirección"
        verbose_name_plural = "direcciones"
        ordering = ["-principal", "-creado"]

    def __str__(self):
        etiqueta = self.alias or self.calle
        return f"{etiqueta} · Col. {self.colonia}, CP {self.codigo_postal}"

    @property
    def texto(self):
        return armar_direccion(
            self.calle, self.colonia, self.codigo_postal,
            self.ciudad, self.entidad, self.referencia,
        )

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Solo una dirección puede ser la principal.
        if self.principal:
            Direccion.objects.filter(usuario=self.usuario).exclude(pk=self.pk).update(principal=False)