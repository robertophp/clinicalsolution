# **Manual de Procesos Clínicos y Preguntas Frecuentes**

**Proyecto:** Ecosistema Digital IA \- Clínica Dental Tu Sonrisa  
**Insumo Operativo:** Base de Conocimiento para Configuración de Agente Virtual  
**Línea de Spacing:** 1.15

## **1\. DIAGNÓSTICO DENTAL**

**Definición:** Evaluación integral para conocer el estado de tu salud bucal y brindarte así el tratamiento adecuado para tener una sonrisa sana.  
**Beneficios:** Detectar a tiempo enfermedades bucales.

### **Preguntas Frecuentes (FAQ):**

* **¿Cada cuánto se debe realizar un diagnóstico dental?** Se recomienda cada 6 meses para detectar enfermedades bucales y poder brindar el tratamiento adecuado.  
* **¿Qué incluye?** Evaluación Clínica, recomendaciones y alternativas en tratamientos dentales según el caso.  
* **¿Es obligatorio la evaluación antes de iniciar un tratamiento?** SÍ, permite realizar un diagnóstico adecuado a su caso.  
* **¿Con qué infraestructura cuenta la clínica para el diagnóstico?** Contamos con un centro de imágenes propio equipado con tecnología alemana de última generación. Esto permite realizar todos tus estudios en un solo lugar, sin necesidad de salir de la clínica.  
* **¿Qué tipo de radiografías o estudios se pueden realizar aquí?** Estamos capacitados para tomar radiografías panorámicas, cefalométricas, tomografías dentales, radiografías palmares y de ATM (Articulación Temporomandibular) con alta resolución.
* **¿Quién realiza las evaluaciones en la clínica?** La Dra. Palacios y su equipo de especialistas altamente calificados realizan las evaluaciones en la clínica.
* **¿Atienden niños y niñas?** ¡Sí! 😊 Atendemos niños y niñas de 6 añitos en adelante 🦷✨ Para menores de 6 años no ofrecemos atención dental por el momento.

Recomendación técnica: `IF` el paciente pregunta si atendemos niños/niñas o menciona que quiere una cita para su hijo/hija u otro menor, `THEN` indica con calidez y emojis que atendemos niños y niñas **de 6 añitos en adelante**. Si la edad supera o iguala los 6 años (o no se menciona), ofrece agendar la cita como tercero (es_para_tercero=true, el niño/niña va como beneficiario). Si la edad es menor a 6 años, declina con empatía sin derivar a otro médico y pregunta si puedes ayudar en algo más. **NUNCA** escribas "nino" o "nina" sin tildes; usa siempre "niño" y "niña".

Recomendación técnica: `IF` el paciente pregunta quién realiza las evaluaciones, confirma que «la Dra. Palacios es quien las hace», o pregunta por la doctora al agendar una evaluación, `THEN` responde que la Dra. Palacios **y su equipo de especialistas altamente calificados** realizan las evaluaciones. **NUNCA** digas que solo la Dra. Palacios atiende o realiza las evaluaciones de forma exclusiva.

Recomendación técnica: `IF` el paciente pregunta por costos de radiografías o si el tratamiento solicitado requiere estudios previos (como ortodoncia o implantes), `THEN` el agente debe recalcar que la clínica cuenta con su propio centro de imágenes alemán, evitando derivar al paciente a laboratorios externos. 

## **2\. LIMPIEZA DENTAL PROFUNDA**

**Definición:** Procedimiento preventivo que elimina placa bacteriana, sarro y manchas acumuladas en los dientes.  
**Beneficios:**

* Previene inflamación y sangrado de encías.  
* Mejora la salud bucal.  
* Ayuda a mantener un aliento fresco.

### **Preguntas Frecuentes (FAQ):**

* **¿Molesta la limpieza dental?** No, este procedimiento se realiza con sistema de ultrasonido y se coloca anestesia tópica para mayor comodidad y confianza de nuestro paciente.  
* **¿Cuándo debo de realizarme la limpieza dental?** Se realiza cada 6 meses para prevenir acumulación de placa bacteriana (sarro).  
* **¿La limpieza blanquea los dientes?** No, pero sí aclara de manera mínima debido a la eliminación de manchas y sarro que esta realiza.

## **3\. RELLENOS DENTALES (RESINAS)**

**Definición:** Tratamiento utilizado para restaurar piezas dentales afectadas por lesiones de caries pequeñas y medianas, devolviendo la estética y función de la pieza.  
**Beneficios:**

* Recuperar la función del diente.  
* Evita el avance de la caries en el diente.  
* Ofrece un resultado estético.

### **Preguntas Frecuentes (FAQ):**

* **¿Cuánto dura una resina?** La resina puede durar de hasta 8 años con una buena higiene y chequeos de rutina cada 6 meses.  
* **¿Puedo comer después de hacerme un relleno?** Sí, puede comer normal, siempre manteniendo el cuidado con alimentos muy duros como dulces, hielo, etc.  
* **¿Puede cambiar de color la resina del relleno?** Sí, con el tiempo pueda cambiar de color por consumo de bebidas y comidas con bastante colorante.  
* **¿Qué tipo de resinas utilizan?** Utilizamos resinas condensadas de alta calidad con marcas reconocidas a nivel internacional como IVOCLAR, 3M y VITTA esto para garantizar la calidad y éxito del tratamiento que se realiza (rellenos).

## **4\. RECUBRIMIENTO PULPAR (BIODENTINE)**

**Definición:** El Biodentine se usa en caries profundas de manera preventiva para proteger el nervio y ayudar a regenerar la dentina, así evitar endodoncias en algunos casos. Se requiere de diagnóstico previo y pruebas pulpares.  
**Precio referencial:** El recubrimiento pulpar (Biodentine) tiene un costo de **USD 145.00** por pieza. La restauración posterior se cotiza aparte según el caso (ver FAQ).  
**Beneficios:**

* Evitar endodoncias y extracciones innecesarias.  
* Mantener la pieza a un largo plazo.  
* Eliminar la sensibilidad o molestia dental.

### **Preguntas Frecuentes (FAQ):**

* **¿Puedo volver a sentir dolor?** No debería, pero si hay síntomas de dolor, se procede a realizar el tratamiento de endodoncia así eliminar de manera definitiva el dolor o sensibilidad dental.  
* **¿Cuánto tiempo puede durar?** Este dura aproximadamente unos 5 a 8 años con una buena higiene y sus chequeos de rutina.  
* **¿Qué tipo de restauración se necesita?** La restauración puede ser desde $45.00 con un relleno de resina. En algunos casos, si el diente presenta mayor daño, puede ser recomendable realizar una incrustación con un valor de $300.00 para una mejor resistencia y durabilidad.

## **5\. ENDODONCIA**

**Definición:** La endodoncia (conocida comúnmente como "matar el nervio") es un tratamiento de odontología conservadora que consiste en extraer la pulpa dental (el nervio y los vasos sanguíneos) cuando está inflamada o infectada.  
**Beneficios:**

* Elimina el dolor.  
* Evita la extracción dental.  
* Se conserva la pieza natural a largo plazo.

### **Preguntas Frecuentes (FAQ):**

* **¿La endodoncia duele?** NO DUELE, el procedimiento se realiza de manera cómoda y segura en nuestros pacientes con equipo y materiales de alta calidad.  
* **¿En cuántas citas se realiza el tratamiento de endodoncia?** EN UNA SOLA CITA, SIN DOLOR. Contamos con equipo de última tecnología que nos permite realizar el tratamiento de una manera segura y cómoda en el menor tiempo.  
* **¿Es importante restaurar la pieza después de la endodoncia?** Sí es importante restaurar ya que nos garantiza el éxito del tratamiento a largo plazo y para esta restauraciones dentales contamos con TECNOLOGÍA CAD CAM 100% ALEMANA y materiales libres de metal.  
* **¿Cuál es la alternativa si no me realizo la endodoncia?** La alternativa sería la extracción de la pieza. Sin embargo, perder un diente natural exige posteriormente desgastar los dientes vecinos para colocar un puente o someterse a un implante dental más costoso. La endodoncia es la vía más conservadora y económica para salvar tu sonrisa.  
* **¿Cómo es el procedimiento de restauración avanzado tras la endodoncia?** Dependiendo del daño, realizamos un protocolo de recuperación de piezas complejas que incluye: remoción de caries, alargamiento de corona (si es necesario), endodoncia, colocación de un poste de fibra de vidrio y reconstrucción con resina reforzada o incrustación digital.

Recomendación técnica **:** `IF` un paciente muestra resistencia o miedo hacia la endodoncia por costo o procedimiento, `THEN` el agente debe activar el argumento de contraste comercial: *Salvar la pieza natural en 1 cita vs. el costo económico y desgaste de una extracción*.

## **6\. EXTRACCIÓN DENTAL (EXODONCIA)**

**Definición:** Procedimiento mediante el cual se retira una pieza dental dañada que no puede ser conservada y representa un riesgo para la salud oral.  
**Beneficios:** Elimina Infecciones y evita complicaciones mayores.

### **Preguntas Frecuentes (FAQ):**

* **¿Duele la extracción?** NO, este procedimiento se realiza bajo anestesia local lo que permite que el paciente no siento dolor ni molestia durante el procedimiento.  
* **¿Cuántos días de recuperación debo de tener?** Son 3 días de recuperación según lo indicado por Dra.  
* **¿Qué debo de hacer después de una extracción?** Se recomienda tener una dieta blanda por 3 días, toma de analgésicos y antibióticos.  
* **¿Cuándo se realiza la extracción?** La extracción se realiza cuando la pieza ya no tiene oportunidad de salvarla con endodoncias por fracturas verticales internas, periodontitis avanzada, o lesiones muy avanzadas en la pieza.
* **¿Necesito radiografía panorámica para extracción de cordales (muelas del juicio)?** Sí, es obligatoria. Si ya la tienes, debes traerla el día de tu cita de evaluación. Si no la tienes, podemos tomártela aquí en la clínica en una cita de evaluación inicial con nuestro equipo radiológico (contamos con centro de imágenes propio con equipo alemán).

## **7\. BLANQUEAMIENTO DENTAL**

**Definición:** Tratamiento estético que aclara el color de los dientes.  
**Beneficios:** Resultados naturales, resultado rápido y cómodo, confianza y seguridad al sonreír.

### **Preguntas Frecuentes (FAQ):**

* **¿Es seguro?** Sí, es un procedimiento rápido y seguro con materiales de alta calidad.  
* **¿Cuánto tiempo dura el blanqueamiento?** Este dura 8 meses, se debe de acudir a citas de mantenimiento.  
* **¿Produce sensibilidad?** Sí puede generar una leve sensibilidad durante los primeros 3 días, luego del procedimiento la Dra. deja productos de mantenimiento para prevención de sensibilidad.  
* **¿En cuantas citas lo realizan?** Es una sola cita aproximadamente de media hora donde también se solventan dudas que puedan existir previo al procedimiento.  
* **¿Quienes pueden realizar este blanqueamiento dental?** Solo se realiza en pacientes con dentura sana y encías sanas, pacientes con dientes naturales; no debe de tener laminados dentales ni coronas dentales o puentes.  
* **¿En cuántas citas lo realizan y cuánto dura la sesión?** Es una sola cita rápida y segura que toma **menos de una hora (aproximadamente 30 a 45 minutos)** en el sillón clínico. Se aplica un gel especial que se activa mediante una lámpara LED para lograr resultados brillantes e inmediatos el mismo día.

### **Restricción de Blanqueamiento**

* **El manual aporta el filtro de seguridad:** Advierte que el blanqueamiento está contraindicado si el paciente tiene laminados, coronas o puentes en la zona.

## **8\. BLANQUEAMIENTO INTERNO**

**Definición:** Es un procedimiento estético mínimamente invasivo que ayuda a devolver el color natural del diente de adentro hacia afuera.  
**Beneficios:** Devuelve la estética natural del diente, conserva la estructura dental y es un procedimiento indoloro.

### **Preguntas Frecuentes (FAQ):**

* **¿En cuantas citas se realiza?** Se requieren de 2 a 3 citas más cada 5 días, ya que se debe de evaluar el tono o renovar el producto y lograr el color deseado.  
* **¿Cuándo debo de realizarlo?** Este se realiza con previa evaluación y normalmente el cambio de color surge cuando hay algún golpe, un tratamiento de endodoncia contaminado y una mala restauración.  
* **¿Cuánto tiempo dura el blanqueamiento interno?** Este tratamiento una vez que se obtiene el color deseado, se mantiene estable sin variaciones en un periodo de 2 a 5 años.

INFORMACIÓN EXTRAÍDA DE LOS VIDEOS

## **9\. CORONAS Y PUENTES CEREC (TECNOLOGÍA CAD/CAM)**

* **Definición:** Restauraciones dentales (fundas o prótesis) diseñadas por computadora y fabricadas en minutos con materiales cerámicos libres de metal de alta estética.  
* **Beneficios:** Tecnología 100% alemana, máxima durabilidad, variedad de colores naturales y eliminación de las pastas o moldes incómodos del pasado.  
* **Preguntas Frecuentes (FAQ):**  
  * **¿Cuánto tiempo toma confeccionar una corona?** ¡Se entrega en una sola cita\! Gracias a nuestro laboratorio digital y al sistema de tallado rápido CEREC, diseñamos, fabricamos e instalamos tu corona en una hora, evitando semanas de espera o el uso de provisionales.  
  * **¿Cuándo se requiere una corona en lugar de una incrustación?** Las coronas se indican únicamente si el paciente ya tiene una corona vieja que desea reemplazar o si el diente está extremadamente destruido. Si la estructura dental lo permite, la *incrustación* es la alternativa más conservadora para proteger el diente.

**Recomendación técnica:** `IF` el usuario consulta por "coronas, puentes o dientes quebrados", `THEN` el agente debe posicionar la ventaja de la "Odontología en una sola cita" mediante tecnología CAD/CAM alemana.

## **10\. LAMINADOS VS. CARILLAS DE ALTA ESTÉTICA**

* **Definición:** Tratamientos de diseño de sonrisa avanzados para modificar la forma, tamaño y color de los dientes mediante finas capas de materiales estéticos.  
* **Preguntas Frecuentes (FAQ):**  
  * **¿Cuál es la diferencia entre Laminados y Carillas?** 1\. *Laminados dentales:* Corrigen manchas, pequeñas fracturas o dientes separados **sin realizar ningún desgaste dental**. Se colocan en un lapso de 1 a 2 horas. 2\. *Carillas (Disilicato de Litio o Porcelana):* Son cerámicas reforzadas de alta estética fabricadas en nuestro propio laboratorio en solo una hora. Requieren un leve desgaste superficial, pero ofrecen resultados mucho más prolongados, duraderos y no necesitan pulido constante.  
  * **¿Cada cuánto tiempo requieren mantenimiento?** Ambos procedimientos exigen citas de revisión clínica y mantenimiento preventivo cada 6 meses.

Recomendación técnica**:** Registrar en el diccionario de sinónimos de la IA el término "Laminados" acoplado a "Carillas". Si el paciente pregunta por diseño de sonrisa, el bot debe ofrecer ambas opciones aclarando que los laminados no desgastan el diente.

## **11\. ORTODONCIA E INVISALIGN**

* **Definición:** Especialidad encargada de corregir la posición de los dientes y problemas de la mordedura para mejorar la función y la estética bajo la dirección de la Dra. Mari Pacheco.  
* **Preguntas Frecuentes (FAQ):**  
  * **¿Qué opciones de ortodoncia manejan?** Contamos con ortodoncia convencional (brackets metálicos y estéticos de zafiro/cerámica) y con el sistema de ortodoncia invisible mediante alineadores transparentes **Invisalign**.

Recomendación técnica**:** Mapear la intención "Invisalign o brackets transparentes". El agente debe confirmar que la clínica cuenta con la certificación oficial de la marca y derivar el flujo para agendar la evaluación de ortodoncia.

## **12\. GINGIVECTOMÍA ESTÉTICA LÁSER**

* **Definición:** Procedimiento estético mínimamente invasivo que consiste en recortar el exceso de encía que cubre los dientes.  
* **Beneficios:** Ideal para corregir la "sonrisa gingival" (mostrar mucha encía al sonreír), encías agrandadas post-ortodoncia o dientes que lucen muy pequeños.  
* **Preguntas Frecuentes (FAQ):**  
  * **¿Es un proceso doloroso?** No, es un tratamiento rápido, sencillo y completamente indoloro gracias al uso de tecnología láser de alta precisión.  
  * **¿En cuántas citas se realiza?** Se ejecuta en una sola cita con resultados estéticos y naturales visibles de manera inmediata.

Recomendación técnica**:** Habilitar el concepto de "recorte de encías / estética gingival". Clasificarlo como un tratamiento estético de una sola sesión e invitar al agendamiento de diagnóstico.

