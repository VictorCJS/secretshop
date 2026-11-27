import re

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import (
    Banner, Categoria, Condicion, Direccion, Franquicia, Linea, Pedido,
    Producto, ProductoImagen, TipoProducto, armar_direccion, clave_normalizada,
)

MAX_FOTOS_POR_VEZ = 8
MAX_FOTOS_ADICIONALES = 12
MAX_MB = 5


def _estilizar(campos):
    for campo in campos.values():
        w = campo.widget
        if isinstance(w, forms.CheckboxSelectMultiple):
            continue
        if isinstance(w, forms.CheckboxInput):
            w.attrs["class"] = "form-check-input"
        elif isinstance(w, forms.Select):
            w.attrs["class"] = "form-select"
        else:
            w.attrs["class"] = "form-control"


class DireccionMixin:
    """Validaciones compartidas por el formulario de compra y el de direcciones."""

    def clean_codigo_postal(self):
        cp = (self.cleaned_data.get("codigo_postal") or "").strip()
        if not re.fullmatch(r"\d{5}", cp):
            raise ValidationError("El código postal debe tener 5 dígitos.")
        return cp

    def clean_telefono(self):
        tel = (self.cleaned_data.get("telefono") or "").strip()
        digitos = re.sub(r"\D", "", tel)
        if not 10 <= len(digitos) <= 13:
            raise ValidationError("Escribe un teléfono de 10 dígitos.")
        return tel


_ETIQUETAS_DIRECCION = {
    "nombre": "Nombre de quien recibe",
    "telefono": "Teléfono",
    "codigo_postal": "Código postal",
    "entidad": "Estado",
    "ciudad": "Ciudad o municipio",
    "colonia": "Colonia",
    "calle": "Dirección (calle y número)",
    "referencia": "Referencias (opcional)",
}


class PedidoForm(DireccionMixin, forms.ModelForm):
    class Meta:
        model = Pedido
        fields = [
            "nombre", "email", "telefono", "codigo_postal", "entidad",
            "ciudad", "colonia", "calle", "referencia",
        ]
        labels = {**_ETIQUETAS_DIRECCION, "email": "Correo electrónico"}
        widgets = {
            "telefono": forms.TextInput(attrs={"inputmode": "tel"}),
            "codigo_postal": forms.TextInput(attrs={
                "inputmode": "numeric", "maxlength": "5", "pattern": "[0-9]{5}",
            }),
            "referencia": forms.TextInput(attrs={
                "placeholder": "Entre qué calles está, color de la casa, portón…",
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nombre, campo in self.fields.items():
            campo.required = nombre != "referencia"
        _estilizar(self.fields)

    def save(self, commit=True):
        pedido = super().save(commit=False)
        d = self.cleaned_data
        # El texto completo se guarda aparte para que lo muestren las pantallas actuales.
        pedido.direccion = armar_direccion(
            d["calle"], d["colonia"], d["codigo_postal"],
            d["ciudad"], d["entidad"], d.get("referencia", ""),
        )
        if commit:
            pedido.save()
        return pedido


class DireccionForm(DireccionMixin, forms.ModelForm):
    class Meta:
        model = Direccion
        fields = [
            "alias", "nombre", "telefono", "codigo_postal", "entidad",
            "ciudad", "colonia", "calle", "referencia", "principal",
        ]
        labels = {
            **_ETIQUETAS_DIRECCION,
            "alias": "Nombre de esta dirección (opcional)",
            "principal": "Usar como mi dirección principal",
        }
        widgets = {
            "alias": forms.TextInput(attrs={"placeholder": "Casa, trabajo…"}),
            "telefono": forms.TextInput(attrs={"inputmode": "tel"}),
            "codigo_postal": forms.TextInput(attrs={
                "inputmode": "numeric", "maxlength": "5", "pattern": "[0-9]{5}",
            }),
            "referencia": forms.TextInput(attrs={
                "placeholder": "Entre qué calles está, color de la casa, portón…",
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for nombre, campo in self.fields.items():
            campo.required = nombre not in ("alias", "referencia", "principal")
        _estilizar(self.fields)


def _obtener_o_crear(modelo, texto):
    """Busca por nombre sin importar mayúsculas ni acentos; si no existe, lo crea."""
    texto = " ".join((texto or "").split())
    if not texto:
        return None
    clave = clave_normalizada(texto)
    for existente in modelo.objects.all():
        if clave_normalizada(existente.nombre) == clave:
            return existente
    return modelo.objects.create(nombre=texto)


# --- Subir varias fotos a la vez (patrón de la documentación de Django) ---

class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        limpiar_uno = super().clean
        if isinstance(data, (list, tuple)):
            return [limpiar_uno(d, initial) for d in data]
        return [limpiar_uno(data, initial)]


class SelectCondicion(forms.Select):
    """Select que guarda en cada opción el nombre de su tipo, para filtrar con JavaScript."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        opcion = super().create_option(name, value, label, selected, index, subindex, attrs)
        instancia = getattr(value, "instance", None)
        if instancia is not None:
            opcion["attrs"]["data-tipo"] = instancia.tipo.nombre
        return opcion


class ProductoForm(forms.ModelForm):
    franquicia_texto = forms.CharField(
        label="Franquicia",
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={
            "list": "lista-franquicias",
            "autocomplete": "off",
            "placeholder": "Escribe para buscar (por ejemplo, One Piece)",
        }),
        help_text="Opcional. Si no existe, se crea al guardar.",
    )
    tipo_texto = forms.CharField(
        label="Tipo de producto",
        max_length=100,
        widget=forms.TextInput(attrs={
            "list": "lista-tipos",
            "autocomplete": "off",
            "placeholder": "Escribe para buscar (por ejemplo, Figuras)",
        }),
        help_text="TCG, Figuras, Ropa… Si no existe, se crea al guardar.",
    )
    linea_texto = forms.CharField(
        label="Línea o marca",
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={
            "list": "lista-lineas",
            "autocomplete": "off",
            "placeholder": "Por ejemplo, Nendoroid, Pop Up Parade o Banpresto",
        }),
        help_text="Opcional. Si no existe, se crea al guardar.",
    )
    condicion = forms.ModelChoiceField(
        label="Estado",
        required=False,
        queryset=Condicion.objects.select_related("tipo"),
        empty_label="— Sin estado —",
        widget=SelectCondicion,
        help_text="Solo aparecen los estados del tipo elegido.",
    )
    imagenes_extra = MultipleFileField(
        label="Más fotos",
        required=False,
        help_text=(
            f"Opcional. Puedes elegir varias a la vez (máximo {MAX_FOTOS_POR_VEZ} por vez, "
            f"{MAX_MB} MB cada una)."
        ),
    )
    eliminar_imagenes = forms.ModelMultipleChoiceField(
        queryset=ProductoImagen.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    field_order = [
        "franquicia_texto", "tipo_texto", "linea_texto", "condicion",
        "detalle_condicion", "nombre", "descripcion", "precio", "precio_oferta",
        "oferta_hasta", "stock", "imagen", "imagenes_extra", "activo",
    ]

    class Meta:
        model = Producto
        fields = [
            "nombre", "descripcion", "precio", "precio_oferta", "oferta_hasta",
            "stock", "imagen", "detalle_condicion", "activo",
        ]
        labels = {
            "imagen": "Foto principal",
            "detalle_condicion": "Detalle del estado",
            "precio": "Precio normal",
            "precio_oferta": "Precio de oferta",
            "oferta_hasta": "La oferta termina el",
        }
        help_texts = {
            "precio_oferta": "Opcional. Debe ser menor al precio normal.",
            "oferta_hasta": "Opcional. Ese día todavía cuenta. Vacío = sin fecha de término.",
        }
        widgets = {
            "descripcion": forms.Textarea(attrs={"rows": 4}),
            "detalle_condicion": forms.TextInput(attrs={
                "placeholder": "Opcional. Por ejemplo: PSA 10, sin caja, esquina marcada",
            }),
            "oferta_hasta": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["condicion"].label_from_instance = lambda obj: obj.nombre
        _estilizar(self.fields)

        if self.instance and self.instance.pk:
            self.fields["eliminar_imagenes"].queryset = self.instance.imagenes.all()
            if self.instance.franquicia_id:
                self.initial["franquicia_texto"] = self.instance.franquicia.nombre
            if self.instance.tipo_id:
                self.initial["tipo_texto"] = self.instance.tipo.nombre
            if self.instance.linea_id:
                self.initial["linea_texto"] = self.instance.linea.nombre
            if self.instance.condicion_id:
                self.initial["condicion"] = self.instance.condicion_id

    def clean_imagen(self):
        imagen = self.cleaned_data.get("imagen")
        if imagen and hasattr(imagen, "content_type") and imagen.size > MAX_MB * 1024 * 1024:
            raise ValidationError(f"La foto no debe pasar de {MAX_MB} MB.")
        return imagen

    def clean_imagenes_extra(self):
        archivos = [f for f in (self.cleaned_data.get("imagenes_extra") or []) if f]
        if len(archivos) > MAX_FOTOS_POR_VEZ:
            raise ValidationError(f"Sube máximo {MAX_FOTOS_POR_VEZ} fotos a la vez.")
        validas = []
        for archivo in archivos:
            if archivo.size > MAX_MB * 1024 * 1024:
                raise ValidationError(f"«{archivo.name}» pesa más de {MAX_MB} MB.")
            try:
                validas.append(forms.ImageField().clean(archivo))
            except ValidationError:
                raise ValidationError(f"«{archivo.name}» no es una imagen válida.")
        return validas

    def clean(self):
        datos = super().clean()

        tipo_texto = datos.get("tipo_texto") or ""
        condicion = datos.get("condicion")
        if condicion and clave_normalizada(condicion.tipo.nombre) != clave_normalizada(tipo_texto):
            self.add_error("condicion", "Ese estado no corresponde al tipo elegido.")

        # Presentación solo para TCG
        presentacion = datos.get("presentacion")
        es_tcg = clave_normalizada(tipo_texto) == clave_normalizada("TCG")
        if presentacion and not es_tcg:
            self.add_error("presentacion", "La presentación solo aplica a productos TCG.")

        if condicion:
            cond = clave_normalizada(condicion.nombre)
            detalle = (datos.get("detalle_condicion") or "").strip()

            if cond == clave_normalizada("Gradeado") and not detalle:
                self.add_error(
                    "detalle_condicion",
                    "Indica la calificadora y la nota (ej. PSA 10).",
                )

            es_sellado = presentacion and presentacion != Producto.Presentacion.CARTA
            if es_sellado and cond != clave_normalizada("Nuevo"):
                self.add_error("condicion", "Los productos sellados solo pueden ser nuevos.")

        nuevas = datos.get("imagenes_extra") or []
        quitar = datos.get("eliminar_imagenes") or []
        actuales = self.instance.imagenes.count() if self.instance.pk else 0
        if actuales - len(quitar) + len(nuevas) > MAX_FOTOS_ADICIONALES:
            self.add_error(
                "imagenes_extra",
                f"Un producto puede tener hasta {MAX_FOTOS_ADICIONALES} fotos adicionales.",
            )
            precio = datos.get("precio")
            oferta = datos.get("precio_oferta")
            if oferta is not None and precio is not None and oferta >= precio:
                self.add_error("precio_oferta", "El precio de oferta debe ser menor al precio normal.")
            if oferta is None and datos.get("oferta_hasta"):
                self.add_error("oferta_hasta", "Para usar una fecha, escribe primero el precio de oferta.")
        return datos

    def save(self, commit=True):
        with transaction.atomic():
            producto = super().save(commit=False)
            producto.franquicia = _obtener_o_crear(
                Franquicia, self.cleaned_data.get("franquicia_texto")
            )
            producto.tipo = _obtener_o_crear(TipoProducto, self.cleaned_data.get("tipo_texto"))
            producto.linea = _obtener_o_crear(Linea, self.cleaned_data.get("linea_texto"))

            # Una línea recién creada (o sin tipo) queda ligada al tipo del producto
            if producto.linea and producto.linea.tipo_id is None and producto.tipo:
                producto.linea.tipo = producto.tipo
                producto.linea.save(update_fields=["tipo"])

            producto.condicion = self.cleaned_data.get("condicion")
            if commit:
                producto.save()
                for foto in self.cleaned_data.get("eliminar_imagenes") or []:
                    foto.imagen.delete(save=False)
                    foto.delete()
                for archivo in self.cleaned_data.get("imagenes_extra") or []:
                    ProductoImagen.objects.create(producto=producto, imagen=archivo)
        return producto
class _EtiquetaForm(forms.ModelForm):
    placeholder = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["nombre"].widget.attrs.update({
            "class": "form-control",
            "placeholder": self.placeholder,
        })


class FranquiciaForm(_EtiquetaForm):
    placeholder = "Nueva franquicia (por ejemplo, Naruto)"

    class Meta:
        model = Franquicia
        fields = ["nombre"]


class TipoForm(_EtiquetaForm):
    placeholder = "Nuevo tipo (por ejemplo, Ropa)"

    class Meta:
        model = TipoProducto
        fields = ["nombre"]


class LineaForm(_EtiquetaForm):
    placeholder = "Línea o marca (por ejemplo, Nendoroid)"

    class Meta:
        model = Linea
        fields = ["nombre"]


class CondicionNombreForm(_EtiquetaForm):
    placeholder = "Nombre del estado"

    class Meta:
        model = Condicion
        fields = ["nombre"]


class CondicionForm(forms.ModelForm):
    class Meta:
        model = Condicion
        fields = ["tipo", "nombre"]
        labels = {"tipo": "Tipo", "nombre": "Estado"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tipo"].empty_label = "— Elige un tipo —"
        self.fields["tipo"].widget.attrs["class"] = "form-select"
        self.fields["nombre"].widget.attrs.update({
            "class": "form-control",
            "placeholder": "Nuevo estado (por ejemplo, Sellado)",
        })


class CategoriaForm(forms.ModelForm):
    class Meta:
        model = Categoria
        fields = ["nombre", "padre"]
        labels = {"padre": "Pertenece a"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["padre"].queryset = Categoria.objects.filter(padre__isnull=True)
        self.fields["padre"].required = False
        self.fields["padre"].empty_label = "— Categoría principal —"
        self.fields["nombre"].widget.attrs["class"] = "form-control"
        self.fields["padre"].widget.attrs["class"] = "form-select"


class BannerForm(forms.ModelForm):
    class Meta:
        model = Banner
        fields = ["imagen", "titulo", "subtitulo", "enlace", "orden", "activo"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _estilizar(self.fields)

    def clean_imagen(self):
        imagen = self.cleaned_data.get("imagen")
        if imagen and hasattr(imagen, "content_type") and imagen.size > 5 * 1024 * 1024:
            raise forms.ValidationError("La imagen no debe pasar de 5 MB.")
        return imagen