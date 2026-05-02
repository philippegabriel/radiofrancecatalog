.PHONY: dump test
KEY=$(file < .OpenAPIKey)
TARGETS:= \
affaires-sensibles.html \
lsd-la-serie-documentaire.html \
les-nuits-de-france-culture.html \
les-pieds-sur-terre.html \
le-cours-de-l-histoire.html \

all: $(TARGETS)
%.db: %.csv
	sqlite3 $@ < schema.sql
	sqlite3 $@ ".import --csv --skip 1 $< rf"
	sqlite3 $@ < addlinktags.sql
%.db.csv: %.db
	sqlite3 -init sqlite3.csv.init $< < query.sql > $@

login: lsd-la-serie-documentaire.db
	sqlite3 -init sqlite3.csv.init lsd-la-serie-documentaire.db
affaires-sensibles.csv:
	$(eval URL=https://www.radiofrance.fr/franceinter/podcasts/affaires-sensibles)
	python rf_dump.py --api-key $(KEY) --show-url $(URL) --out  $@
lsd-la-serie-documentaire.csv:
	$(eval URL=https://www.radiofrance.fr/franceculture/podcasts/lsd-la-serie-documentaire)
	python rf_dump.py --api-key $(KEY) --show-url $(URL) --out  $@
les-nuits-de-france-culture.csv:
	$(eval URL=https://www.radiofrance.fr/franceculture/podcasts/les-nuits-de-france-culture)
	python rf_dump.py --api-key $(KEY) --show-url $(URL) --out  $@
les-pieds-sur-terre.csv:
	$(eval URL=https://www.radiofrance.fr/franceculture/podcasts/les-pieds-sur-terre)
	python rf_dump.py --api-key $(KEY) --show-url $(URL) --out  $@
le-cours-de-l-histoire.csv:
	$(eval URL=https://www.radiofrance.fr/franceculture/podcasts/le-cours-de-l-histoire)
	python rf_dump.py --api-key $(KEY) --show-url $(URL) --out  $@
%.html: %.db.csv
	echo '<link rel="stylesheet" href="index.css">' > $@
	python csv2html.py < $< >> $@
test:
	@echo $(TARGETS)
	@echo $(addsuffix .db,$(basename $(TARGETS)))
	@echo $(addsuffix .db.csv,$(basename $(TARGETS)))
clean:
	rm -rf $(TARGETS)
	rm -rf $(addsuffix .db,$(basename $(TARGETS)))
	rm -rf $(addsuffix .db.csv,$(basename $(TARGETS)))

