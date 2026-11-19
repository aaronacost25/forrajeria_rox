from django.db import models
from cloudinary.models import CloudinaryField

class Categoria(models.Model):
    nombre = models.CharField(max_length=100)

    def __str__(self):
        return self.nombre


class SubCategoria(models.Model):
    nombre = models.CharField(max_length=100)
    categoria = models.ForeignKey(Categoria, on_delete=models.CASCADE)

    def __str__(self):
        return f"{self.categoria.nombre} - {self.nombre}"

class Producto(models.Model):
    nombre = models.CharField(max_length=200)
    categoria = models.ForeignKey(Categoria, on_delete=models.CASCADE)
    subcategoria = models.ForeignKey(SubCategoria, on_delete=models.CASCADE, null=True, blank=True)
    precio = models.DecimalField(max_digits=10, decimal_places=2)
    stock= models.IntegerField(default=0)
    descripcion = models.TextField()
    imagen = CloudinaryField('image', null=True, blank=True)
    destacado = models.BooleanField(default=False)
    oferta = models.BooleanField(default=False)


    def __str__(self):
        return self.nombre
    

class GrupoVariante(models.Model):
    nombre = models.CharField(max_length=100)

    def __str__(self):
        return self.nombre


class OpcionVariante(models.Model):
    grupo = models.ForeignKey(GrupoVariante, on_delete=models.CASCADE)
    nombre = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.grupo.nombre}: {self.nombre}"


class VarianteProducto(models.Model):
    producto = models.ForeignKey(
        Producto,
        on_delete=models.CASCADE,
        related_name="variantes"
    )

    opciones = models.ManyToManyField(
        OpcionVariante,
        blank=True
    )

    precio_extra = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0
    )

    stock = models.PositiveIntegerField(default=0)

    def __str__(self):
        if self.producto_id:
            return f"Variante de {self.producto.nombre}"

        return "Nueva variante" 