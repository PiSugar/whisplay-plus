.PHONY: all clean display sound overlays

all: display sound

display:
	$(MAKE) -C display/driver

sound:
	$(MAKE) -C sound

overlays:
	cpp -nostdinc -undef -x assembler-with-cpp overlay/whisplay-plus.dts | \
		dtc -@ -I dts -O dtb -o overlay/whisplay-plus.dtbo -

clean:
	$(MAKE) -C display/driver clean
	$(MAKE) -C sound clean
	rm -f overlay/whisplay-plus.dtbo
