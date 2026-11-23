from django import forms
from django.db import transaction

from .models import Banner, Categoria, Franquicia, Pedido, Producto, TipoProducto


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

    field_order = [
        "franquicia_texto", "tipo_texto", "nombre", "descripcion",
        "precio", "stock", "imagen", "activo",
    ]

    class Meta:
        model = Producto
        fields = ["nombre", "descripcion", "precio", "stock", "imagen", "activo"]
        widgets = {"descripcion": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            if isinstance(campo.widget, forms.CheckboxInput):
                campo.widget.attrs["class"] = "form-check-input"
            else:
                campo.widget.attrs["class"] = "form-control"

        # Al editar, se rellenan la franquicia y el tipo actuales.
        if self.instance and self.instance.pk:
            if self.instance.franquicia_id:
                self.initial["franquicia_texto"] = self.instance.franquicia.nombre
            if self.instance.tipo_id:
                self.initial["tipo_texto"] = self.instance.tipo.nombre

    def clean_imagen(self):
        imagen = self.cleaned_data.get("imagen")
        if imagen and hasattr(imagen, "content_type") and imagen.size > 5 * 1024 * 1024:
            raise forms.ValidationError("La imagen no debe pasar de 5 MB.")
        return imagen

    def save(self, commit=True):
        with transaction.atomic():
            producto = super().save(commit=False)
            producto.franquicia = _obtener_o_crear(
                Franquicia, self.cleaned_data.get("franquicia_texto")
            )
            producto.tipo = _obtener_o_crear(
                TipoProducto, self.cleaned_data.get("tipo_texto")
            )
            if commit:
                producto.save()
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