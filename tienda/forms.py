from django import forms
from .models import Pedido, Producto, Categoria


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
class ProductoForm(forms.ModelForm):
    class Meta:
        model = Producto
        fields = ["categoria", "nombre", "descripcion", "precio", "stock", "imagen", "activo"]
        widgets = {"descripcion": forms.Textarea(attrs={"rows": 4})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            if isinstance(campo.widget, forms.CheckboxInput):
                campo.widget.attrs["class"] = "form-check-input"
            elif isinstance(campo.widget, forms.Select):
                campo.widget.attrs["class"] = "form-select"
            else:
                campo.widget.attrs["class"] = "form-control"

    def clean_imagen(self):
        imagen = self.cleaned_data.get("imagen")
        # Solo se revisa cuando se sube un archivo nuevo.
        if imagen and hasattr(imagen, "content_type"):
            if imagen.size > 5 * 1024 * 1024:
                raise forms.ValidationError("La imagen no debe pasar de 5 MB.")
        return imagen


class CategoriaForm(forms.ModelForm):
    class Meta:
        model = Categoria
        fields = ["nombre"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["nombre"].widget.attrs["class"] = "form-control"
        self.fields["nombre"].widget.attrs["placeholder"] = "Nombre de la categoría"