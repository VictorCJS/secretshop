from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import (
    Banner, Categoria, Condicion, Franquicia, Linea, Pedido,
    Producto, ProductoImagen, TipoProducto,
)

MAX_FOTOS_POR_VEZ = 8
MAX_FOTOS_ADICIONALES = 12
MAX_MB = 5


class PedidoForm(forms.ModelForm):
    class Meta:
        model = Pedido
        fields = ["nombre", "email", "direccion", "telefono"]
        widgets = {"direccion": forms.Textarea(attrs={"rows": 3})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["email"].required = True
        for campo in self.fields.values():
            campo.widget.attrs["class"] = "form-control"


def _obtener_o_crear(modelo, texto):
    texto = " ".join((texto or "").split())
    if not texto:
        return None
    existente = modelo.objects.filter(nombre__iexact=texto).first()
    return existente or modelo.objects.create(nombre=texto)


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
        label="Línea o marca",
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={
            "list": "lista-franquicias",
            "autocomplete": "off",
            "placeholder": "Por ejemplo, Nendoroid, Pop Up Parade o Banpresto",
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
        label="Línea o formato",
        required=False,
        max_length=100,
        widget=forms.TextInput(attrs={
            "list": "lista-lineas",
            "autocomplete": "off",
            "placeholder": "Por ejemplo, Nendoroid o Pop Up Parade",
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
        "detalle_condicion", "nombre", "descripcion", "precio", "stock",
        "imagen", "imagenes_extra", "activo",
    ]

    class Meta:
        model = Producto
        fields = [
            "nombre", "descripcion", "precio", "stock",
            "imagen", "detalle_condicion", "activo",
        ]
        labels = {
            "imagen": "Foto principal",
            "detalle_condicion": "Detalle del estado",
        }
        widgets = {
            "descripcion": forms.Textarea(attrs={"rows": 4}),
            "detalle_condicion": forms.TextInput(attrs={
                "placeholder": "Opcional. Por ejemplo: PSA 10, sin caja, esquina marcada",
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["condicion"].label_from_instance = lambda obj: obj.nombre

        for campo in self.fields.values():
            if isinstance(campo.widget, forms.CheckboxSelectMultiple):
                continue
            if isinstance(campo.widget, forms.CheckboxInput):
                campo.widget.attrs["class"] = "form-check-input"
            elif isinstance(campo.widget, forms.Select):
                campo.widget.attrs["class"] = "form-select"
            else:
                campo.widget.attrs["class"] = "form-control"

        # Al editar, se rellenan los datos actuales.
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

        tipo_texto = " ".join((datos.get("tipo_texto") or "").split())
        condicion = datos.get("condicion")
        if condicion and condicion.tipo.nombre.lower() != tipo_texto.lower():
            self.add_error("condicion", "Ese estado no corresponde al tipo elegido.")

        nuevas = datos.get("imagenes_extra") or []
        quitar = datos.get("eliminar_imagenes") or []
        actuales = self.instance.imagenes.count() if self.instance.pk else 0
        if actuales - len(quitar) + len(nuevas) > MAX_FOTOS_ADICIONALES:
            self.add_error(
                "imagenes_extra",
                f"Un producto puede tener hasta {MAX_FOTOS_ADICIONALES} fotos adicionales.",
            )
        return datos

    def save(self, commit=True):
        with transaction.atomic():
            producto = super().save(commit=False)
            producto.franquicia = _obtener_o_crear(
                Franquicia, self.cleaned_data.get("franquicia_texto")
            )
            producto.tipo = _obtener_o_crear(TipoProducto, self.cleaned_data.get("tipo_texto"))
            producto.linea = _obtener_o_crear(Linea, self.cleaned_data.get("linea_texto"))
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
        for campo in self.fields.values():
            if isinstance(campo.widget, forms.CheckboxInput):
                campo.widget.attrs["class"] = "form-check-input"
            else:
                campo.widget.attrs["class"] = "form-control"

    def clean_imagen(self):
        imagen = self.cleaned_data.get("imagen")
        if imagen and hasattr(imagen, "content_type") and imagen.size > 5 * 1024 * 1024:
            raise forms.ValidationError("La imagen no debe pasar de 5 MB.")
        return imagen